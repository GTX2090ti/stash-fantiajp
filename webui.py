#!/usr/bin/env python3
# FantiaJp WebUI -- local web front-end for the fantiajp.py scraper.
# Copyright (C) 2026 stash-fantiajp contributors
#
# Licensed under the GNU Affero General Public License v3.0
# (derived from fantiajp.py, same license). See LICENSE.
"""Zero-dependency WebUI for the FantiaJp Stash scraper.

Run:  python webui.py            (then open http://127.0.0.1:8799)

Features
--------
* paste one URL/id per line -> batch scrape -> result cards (cover, tags,
  performers, details) -> export JSON
* batch engine: concurrent workers (1-8), adaptive rate limiting
  (interval doubles on 403/429, decays back after 5 successes),
  live progress via polling, cancel button, duplicate-line dedup
* CookieCloud settings editable in the browser (stored in webui_config.json
  next to this script -- gitignored, never leaves the machine)
* cookie cache status + one-click clear
* reuses fantiajp.py in this directory (same session/cookie logic as Stash)

Env vars still work: WEBUI_HOST / WEBUI_PORT, HTTPS_PROXY etc. are honoured
by requests for fantia.jp traffic (CookieCloud always bypasses the proxy).
"""

import json
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fantiajp  # noqa: E402  (same directory)

CONFIG_FILE = os.path.join(HERE, "webui_config.json")

DEFAULTS = {
    "cc_url": "",
    "cc_key": "",
    "cc_password": "",
    "proxy": "",        # e.g. http://192.168.2.210:7890 ; empty = direct
    "host": "127.0.0.1",
    "port": 8799,
    "workers": 3,       # concurrent scrape threads (1-8)
    "interval": 0.6,    # base min interval between fantia requests (s)
}

def load_config():
    cfg = dict(DEFAULTS)
    # fall back to the defaults baked into fantiajp.py (env vars are read at
    # its import time) -- e.g. a private NAS build may ship CC defaults there
    for k, v in (("cc_url", fantiajp.CC_URL), ("cc_key", fantiajp.CC_KEY),
                 ("cc_password", fantiajp.CC_PASSWORD)):
        if v:
            cfg[k] = v
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                cfg.update({k: v for k, v in json.load(fh).items()
                            if k in DEFAULTS})
        except (OSError, ValueError):
            pass
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)


def apply_config(cfg):
    """Push config into the fantiajp module + process env (hot reload)."""
    fantiajp.CC_URL = cfg["cc_url"]
    fantiajp.CC_KEY = cfg["cc_key"]
    fantiajp.CC_PASSWORD = cfg["cc_password"]
    if cfg["proxy"]:
        # empty setting = keep whatever the host/container already exports
        # (e.g. the Stash-style container proxy), so we never clear it here
        os.environ["HTTPS_PROXY"] = cfg["proxy"]
        os.environ["HTTP_PROXY"] = cfg["proxy"]


def cache_info():
    try:
        st = os.stat(fantiajp.CC_CACHE)
        age = time.time() - st.st_mtime
        with open(fantiajp.CC_CACHE, "r", encoding="utf-8") as fh:
            ck = json.load(fh).get("cookie", "")
        return {"exists": True, "age_sec": int(age), "cookie_len": len(ck),
                "has_session": "_session_id" in ck}
    except OSError:
        return {"exists": False}


def status_payload(cfg):
    ci = cache_info()
    return {
        "cc_configured": bool(cfg["cc_url"] and cfg["cc_key"]
                              and cfg["cc_password"]),
        "cc_url_masked": (cfg["cc_url"] or "") + "  key:***"
                         if cfg["cc_key"] else "",
        "proxy": cfg["proxy"] or os.environ.get("HTTPS_PROXY", ""),
        "cache": ci,
        "port": cfg["port"],
        "workers": cfg["workers"],
        "interval": cfg["interval"],
    }


RE_ID_ONLY = fantiajp.RE_BARE_ID


def resolve_post_id(text):
    text = (text or "").strip()
    if not text:
        return None
    m = RE_ID_ONLY.match(text)
    if m:
        return m.group(1)
    m = fantiajp.RE_POST_IN_URL.search(text)
    if m:
        return m.group(1)
    m = fantiajp.RE_ID_IN_TEXT.search(text)
    if m and "fantia" in text.lower():
        return m.group(1)
    return None


class RateLimiter:
    """Global pacer for fantia requests with adaptive throttling.

    `wait()` blocks until the next slot.  A transient failure (403/429/
    network) doubles the interval (cap 8 s); after 5 consecutive successes
    the interval decays halfway back toward the configured base.
    """

    CAP = 8.0

    def __init__(self, base):
        self.base = max(0.05, float(base))
        self.cur = self.base
        self._next = 0.0
        self._lock = threading.Lock()
        self._streak = 0

    def wait(self):
        with self._lock:
            now = time.time()
            slot = max(self._next, now)
            self._next = slot + self.cur
        delay = slot - time.time()
        if delay > 0:
            time.sleep(delay)

    def penalize(self):
        with self._lock:
            self.cur = min(self.CAP, max(self.cur * 2, self.base * 2))
            self._streak = 0

    def reward(self):
        with self._lock:
            self._streak += 1
            if self._streak >= 5 and self.cur > self.base:
                self.cur = max(self.base, self.cur / 2)
                self._streak = 0


def _transient(msg):
    if not msg:
        return False
    return ("request failed" in msg or "HTTP 403" in msg
            or "HTTP 429" in msg or "HTTP 5" in msg)


# --------------------------------------------------------------------------- #
# Batch job engine: concurrent, rate-limited, cancellable, resumable via polling
# --------------------------------------------------------------------------- #
JOBS = {}           # job_id -> state dict
JOBS_LOCK = threading.Lock()
MAX_ITEMS = 500


def parse_items(items):
    """Resolve lines -> [(raw, pid_or_None)]; dedupe post ids, keep order."""
    seen, dups = set(), 0
    out = []
    for raw in items:
        pid = resolve_post_id(raw)
        if pid and pid in seen:
            dups += 1
            out.append((raw, None, "duplicate of an earlier line, skipped"))
            continue
        if pid:
            seen.add(pid)
        out.append((raw, pid, None))
    return out, dups


def start_job(items, cfg):
    parsed, dups = parse_items(items[:MAX_ITEMS])
    jid = uuid.uuid4().hex[:12]
    job = {
        "id": jid, "state": "running", "total": len(parsed), "done": 0,
        "ok": 0, "fail": 0, "dups": dups, "cancel": False,
        "results": [None] * len(parsed), "started": time.time(),
    }
    with JOBS_LOCK:
        # keep only the 4 most recent jobs around
        for old in sorted(JOBS, key=lambda k: JOBS[k]["started"])[:-4]:
            JOBS.pop(old, None)
        JOBS[jid] = job
    threading.Thread(target=run_job, args=(job, parsed, cfg),
                     daemon=True).start()
    return job


def run_job(job, parsed, cfg):
    limiter = RateLimiter(cfg["interval"])
    workers = max(1, min(8, int(cfg["workers"])))
    tl = threading.local()          # per-thread session (cookie jar safety)

    def get_sess():
        if getattr(tl, "sess", None) is None:
            tl.sess = fantiajp.new_session()
            tl.csrf = fantiajp.get_csrf(tl.sess)
        return tl.sess, tl.csrf

    def one(idx, raw, pid):
        if job["cancel"]:
            return
        rec = {"idx": idx, "input": raw, "ok": False, "post_id": pid,
               "error": None}
        try:
            sess, csrf = get_sess()
            limiter.wait()
            frag = fantiajp.scrape_id(pid, sess, [csrf])
            rec["ok"] = bool(frag)
            rec["data"] = frag
            rec["error"] = None if frag else \
                "scraped empty (not visible / throttled?)"
        except Exception as e:  # noqa: BLE001
            rec["error"] = "%s: %s" % (type(e).__name__, e)
        if _transient(rec["error"]):
            limiter.penalize()
        else:
            limiter.reward()
        finish(rec)

    def finish(rec):
        job["results"][rec["idx"]] = rec
        job["done"] += 1
        if rec["ok"]:
            job["ok"] += 1
        else:
            job["fail"] += 1

    # pre-fill non-resolvable lines so progress counts them immediately
    for idx, (raw, pid, err) in enumerate(parsed):
        if err or not pid:
            job["results"][idx] = {"idx": idx, "input": raw, "ok": False,
                                   "post_id": None, "error":
                                   err or "no fantia post id found"}
    job["done"] = sum(1 for r in job["results"] if r is not None)
    job["fail"] = job["done"]

    todo = [(i, raw, pid) for i, (raw, pid, _e) in enumerate(parsed) if pid]
    if todo:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(one, i, raw, pid) for i, raw, pid in todo]
            for f in futs:
                f.result()
    job["state"] = "cancelled" if job["cancel"] else "done"
    job["finished"] = time.time()


def job_payload(job):
    with JOBS_LOCK:
        results = [r for r in job["results"] if r is not None]
        return {"id": job["id"], "state": job["state"],
                "total": job["total"], "done": job["done"],
                "ok": job["ok"], "fail": job["fail"], "dups": job["dups"],
                "elapsed": round((job.get("finished") or time.time())
                                 - job["started"], 1),
                "results": results}


PAGE = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FantiaJp WebUI</title>
<style>
:root{--bg:#0b1220;--panel:#101a2e;--panel2:#16233d;--line:#24344f;
--txt:#dce6f5;--dim:#8fa3c0;--blue:#3f8cff;--blue2:#2f6fd6;--ok:#38c98e;--err:#ff6b6b}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);
font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;padding:24px}
.wrap{max-width:960px;margin:0 auto}
header{display:flex;align-items:center;gap:10px;margin-bottom:18px}
header svg{width:26px;height:26px;fill:var(--blue)}
h1{font-size:18px;font-weight:600}
h1 small{color:var(--dim);font-weight:400;margin-left:8px;font-size:12px}
.card{background:var(--panel);border:1px solid var(--line);
border-radius:10px;padding:16px;margin-bottom:16px}
.card h2{font-size:13px;color:var(--dim);text-transform:uppercase;
letter-spacing:.08em;margin-bottom:10px;display:flex;align-items:center;gap:6px}
.card h2 svg{width:14px;height:14px;fill:var(--dim)}
textarea{width:100%;min-height:110px;background:var(--panel2);color:var(--txt);
border:1px solid var(--line);border-radius:8px;padding:10px;
font:13px/1.5 Consolas,monospace;resize:vertical}
textarea:focus{outline:1px solid var(--blue)}
.row{display:flex;gap:10px;margin-top:10px;align-items:center;flex-wrap:wrap}
button{background:var(--blue);color:#fff;border:0;border-radius:8px;
padding:8px 18px;font-size:14px;cursor:pointer;display:inline-flex;
align-items:center;gap:6px}
button:hover{background:var(--blue2)}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--dim)}
button.ghost:hover{color:var(--txt);border-color:var(--blue)}
button svg{width:14px;height:14px;fill:#fff}
#st{font-size:12px;color:var(--dim)}
#st b.ok{color:var(--ok)} #st b.err{color:var(--err)}
.res{border:1px solid var(--line);border-radius:10px;background:var(--panel);
margin-bottom:12px;display:flex;overflow:hidden}
.res img{width:180px;object-fit:cover;background:var(--panel2);min-height:120px}
.res .body{padding:12px 14px;flex:1;min-width:0}
.res .t{font-size:15px;font-weight:600;margin-bottom:4px}
.res .meta{color:var(--dim);font-size:12px;margin-bottom:6px}
.tag{display:inline-block;background:var(--panel2);border:1px solid var(--line);
color:var(--dim);border-radius:20px;padding:1px 10px;font-size:12px;margin:2px 4px 2px 0}
.pf{color:var(--txt)}
.res.err{border-left:3px solid var(--err)}
.res.ok{border-left:3px solid var(--ok)}
details{margin-top:6px} details summary{color:var(--blue);cursor:pointer;font-size:12px}
details pre{white-space:pre-wrap;background:var(--panel2);padding:8px;
border-radius:6px;font-size:11px;max-height:260px;overflow:auto}
.set{display:grid;grid-template-columns:150px 1fr;gap:8px;align-items:center;margin:6px 0}
.set label{color:var(--dim);font-size:13px}
.set input{background:var(--panel2);color:var(--txt);border:1px solid var(--line);
border-radius:6px;padding:7px 10px;font-size:13px;width:100%}
.set input:focus{outline:1px solid var(--blue)}
.hint{color:var(--dim);font-size:12px;margin-top:8px}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--blue);
border-top-color:transparent;border-radius:50%;animation:sp .8s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
</style></head><body><div class="wrap">
<header>
<svg viewBox="0 0 24 24"><path d="M12 2C6.5 2 2 6.1 2 11.2c0 2.9 1.4 5.4 3.7 7.1V22l3.4-1.9c.9.3 1.9.4 2.9.4 5.5 0 10-4.1 10-9.2S17.5 2 12 2zm1.1 12.4-2.6-2.7-5 2.7 5.5-5.8 2.6 2.7 4.9-2.7-5.4 5.8z"/></svg>
<h1>FantiaJp WebUI<small>stash scraper front-end</small></h1>
<span style="flex:1"></span>
<button class="ghost" onclick="openSettings()"><svg viewBox="0 0 24 24"><path d="M12 15.5A3.5 3.5 0 0 1 8.5 12 3.5 3.5 0 0 1 12 8.5a3.5 3.5 0 0 1 3.5 3.5 3.5 3.5 0 0 1-3.5 3.5m7.4-2.6c.04-.3.06-.6.06-.9s-.02-.6-.06-.9l2-1.6c.2-.14.25-.4.13-.62l-1.9-3.3a.49.49 0 0 0-.6-.21l-2.4 1a7.3 7.3 0 0 0-1.6-.94l-.36-2.5A.5.5 0 0 0 14.7 3h-3.8a.5.5 0 0 0-.5.42l-.35 2.5c-.57.24-1.1.56-1.6.94l-2.4-1a.49.49 0 0 0-.6.21l-1.9 3.3c-.06.1-.09.22-.07.34s.08.22.18.29l2 1.6a7 7 0 0 0 0 1.8l-2 1.6a.5.5 0 0 0-.12.62l1.9 3.3c.12.22.38.3.6.21l2.4-1c.5.38 1.03.7 1.6.94l.35 2.5c.05.24.26.42.5.42h3.8c.25 0 .46-.18.5-.42l.35-2.5c.57-.24 1.1-.56 1.6-.94l2.4 1c.22.09.48 0 .6-.21l1.9-3.3a.5.5 0 0 0-.12-.62l-2-1.6z"/></svg>CookieCloud 设置</button>
</header>

<div class="card">
<h2><svg viewBox="0 0 24 24"><path d="M19 13h-6v6h-2v-6H5v-2h6V5h2v6h6z"/></svg>刮削输入</h2>
<textarea id="inp" placeholder="每行一个：帖子 URL、纯数字 ID 或含 ID 的文件名&#10;https://fantia.jp/posts/1180318&#10;1180318&#10;FANTIA-976153.mp4"></textarea>
<div class="row">
<button onclick="go()"><svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg>开始刮削</button>
<button class="ghost" id="btnCancel" style="display:none" onclick="cancelJob()"><svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2.4" fill="none"/></svg>停止</button>
<button class="ghost" onclick="clearCache()">清除 cookie 缓存</button>
<button class="ghost" onclick="dlJson()">导出 JSON</button>
<span id="st">加载中…</span>
</div>
</div>

<div id="out"></div>

<div class="card" id="setcard" style="display:none">
<h2><svg viewBox="0 0 24 24"><path d="M12 15.5A3.5 3.5 0 0 1 8.5 12 3.5 3.5 0 0 1 12 8.5a3.5 3.5 0 0 1 3.5 3.5 3.5 3.5 0 0 1-3.5 3.5m7.4-2.6c.04-.3.06-.6.06-.9s-.02-.6-.06-.9l2-1.6c.2-.14.25-.4.13-.62l-1.9-3.3a.49.49 0 0 0-.6-.21l-2.4 1a7.3 7.3 0 0 0-1.6-.94l-.36-2.5A.5.5 0 0 0 14.7 3h-3.8a.5.5 0 0 0-.5.42l-.35 2.5c-.57.24-1.1.56-1.6.94l-2.4-1a.49.49 0 0 0-.6.21l-1.9 3.3c-.06.1-.09.22-.07.34s.08.22.18.29l2 1.6a7 7 0 0 0 0 1.8l-2 1.6a.5.5 0 0 0-.12.62l1.9 3.3c.12.22.38.3.6.21l2.4-1c.5.38 1.03.7 1.6.94l.35 2.5c.05.24.26.42.5.42h3.8c.25 0 .46-.18.5-.42l.35-2.5c.57-.24 1.1-.56 1.6-.94l2.4 1c.22.09.48 0 .6-.21l1.9-3.3a.5.5 0 0 0-.12-.62l-2-1.6z"/></svg>CookieCloud / 代理设置</h2>
<div class="set"><label>CookieCloud 地址</label><input id="c_url" placeholder="http://192.168.x.x:8188"></div>
<div class="set"><label>KEY (UUID)</label><input id="c_key"></div>
<div class="set"><label>同步密码</label><input id="c_pwd" type="password"></div>
<div class="set"><label>出站代理</label><input id="c_proxy" placeholder="http://192.168.2.210:7890（留空 = 直连）"></div>
<div class="set"><label>并发数</label><input id="c_workers" type="number" min="1" max="8" step="1" placeholder="3（1-8）"></div>
<div class="set"><label>请求间隔 (秒)</label><input id="c_interval" type="number" min="0.1" max="5" step="0.1" placeholder="0.6（被限流时自动加倍）"></div>
<div class="row"><button onclick="saveCfg()">保存</button><span class="hint">保存在本目录 webui_config.json（已被 gitignore，不入仓库）。CookieCloud 请求始终绕过代理；代理只用于访问 fantia.jp。遇到 403/429 间隔自动翻倍，连续成功后逐步回落。</span></div>
</div>
</div>
<script>
let CFG={}, LAST=[], CURJOB=null, POLLT=null, RENDERED=new Set();
const $=id=>document.getElementById(id);
function esc(s){return (s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function status(){try{
  const r=await fetch('/api/status');const s=await r.json();
  const ck=s.cache&&s.cache.exists;
  let t='CookieCloud: '+(s.cc_configured?'<b class="ok">已配置</b>':'<b class="err">未配置</b>');
  t+=' | cookie缓存: '+(ck?('<b class="ok">'+(s.cache.has_session?'有会话':'无会话')+' · '+Math.round(s.cache.age_sec/60)+' 分钟前</b>'):'<b class="err">无</b>');
  t+=' | 代理: '+(s.proxy?esc(s.proxy):'直连');
  $('st').innerHTML=t;
}catch(e){$('st').innerHTML='<b class="err">状态获取失败</b>'}}
async function loadCfg(){const c=await (await fetch('/api/config_view')).json();
  $('c_url').value=c.cc_url||'';$('c_key').value=c.cc_key||'';$('c_pwd').value=c.cc_password||'';
  $('c_workers').value=c.workers||3;$('c_interval').value=c.interval||0.6;
  const s=await (await fetch('/api/status')).json();$('c_proxy').value=s.proxy||'';}
function openSettings(){$('setcard').style.display=$('setcard').style.display==='none'?'block':'none'}
async function saveCfg(){const body={cc_url:$('c_url').value.trim(),cc_key:$('c_key').value.trim(),
  cc_password:$('c_pwd').value,proxy:$('c_proxy').value.trim(),
  workers:+$('c_workers').value||3,interval:+$('c_interval').value||0.6};
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  await status();openSettings()}
async function clearCache(){await fetch('/api/cache/clear',{method:'POST'});await status()}
async function go(){
  const items=$('inp').value.split('\\n').map(s=>s.trim()).filter(Boolean);
  if(!items.length)return;
  $('st').innerHTML='<span class="spin"></span> 提交中…';
  $('out').innerHTML='';LAST=[];RENDERED.clear();
  try{
    const r=await fetch('/api/scrape',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({items})});
    const d=await r.json();
    if(d.error){$('st').innerHTML='<b class="err">'+esc(d.error)+'</b>';return}
    CURJOB=d.job_id;
    $('st').innerHTML='<span class="spin"></span> 刮削中 0/'+d.total+(d.dups?' · 去重 '+d.dups+' 条':'');
    $('btnCancel').style.display='inline-flex';
    poll();
  }catch(e){$('st').innerHTML='<b class="err">'+esc(String(e))+'</b>'}
}
async function poll(){
  if(!CURJOB)return;
  try{
    const j=await (await fetch('/api/job/'+CURJOB)).json();
    for(const it of j.results){if(!RENDERED.has(it.idx)){RENDERED.add(it.idx);LAST.push(it);render(it)}}
    if(j.state==='running'){
      $('st').innerHTML='<span class="spin"></span> 刮削中 '+j.done+'/'+j.total
        +' · <b class="ok">'+j.ok+'</b> 成功 · <b class="'+(j.fail?'err':'ok')+'">'+j.fail+'</b> 失败'
        +(j.dups?' · 去重 '+j.dups+' 条':'');
      POLLT=setTimeout(poll,700);return;
    }
    $('st').innerHTML=(j.state==='cancelled'?'<b class="err">已停止</b> · ':'完成：')
      +'<b class="ok">'+j.ok+'</b> 成功 / <b class="'+(j.fail?'err':'ok')+'">'+j.fail+'</b> 失败'
      +(j.dups?' · 去重 '+j.dups+' 条':'')+' · 用时 '+j.elapsed+'s';
  }catch(e){$('st').innerHTML='<b class="err">'+esc(String(e))+'</b>'}
  CURJOB=null;$('btnCancel').style.display='none';await status();
}
async function cancelJob(){if(CURJOB){await fetch('/api/job/'+CURJOB+'/cancel',{method:'POST'});
  $('st').innerHTML='<span class="spin"></span> 停止中…'}}
function render(it){
  const div=document.createElement('div');div.className='res '+(it.ok?'ok':'err');
  if(it.ok){const d=it.data;
    const tags=(d.tags||[]).map(t=>'<span class="tag">'+esc(t.name)+'</span>').join('');
    const pfs=(d.performers||[]).map(p=>'<span class="pf">'+esc(p.name)+'</span>').join('、');
    div.innerHTML='<img src="'+esc(d.image||'')+'" referrerpolicy="no-referrer" onerror="this.style.visibility=\\'hidden\\'">'
     +'<div class="body"><div class="t">'+esc(d.title)+'</div>'
     +'<div class="meta">'+esc(d.code||it.post_id||'')+' · '+esc(d.date||'')+' · '+esc((d.studio||{}).name||'')+'</div>'
     +'<div>'+pfs+'</div><div>'+tags+'</div>'
     +'<details><summary>详情 / JSON</summary><pre>'+esc(d.details||'')+'</pre><pre>'+esc(JSON.stringify(d,null,2))+'</pre></details>'
     +'</div>';}
  else{div.innerHTML='<div class="body"><div class="t">失败'+(it.error&&it.error.indexOf('duplicate')===0?'（重复）':'')+'</div><div class="meta">'+esc(it.input)+'</div><div class="meta">'+esc(it.error||'')+'</div></div>';}
  $('out').appendChild(div)}
function dlJson(){if(!LAST.length)return;const ok=LAST.filter(x=>x.ok).map(x=>x.data);
  const b=new Blob([JSON.stringify(ok,null,2)],{type:'application/json'});
  const a=document.createElement('a');a.href=URL.createObjectURL(b);
  a.download='fantia_scrape_'+Date.now()+'.json';a.click()}
status();loadCfg();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _page(self):
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        cfg = load_config()
        if self.path == "/" or self.path.startswith("/index"):
            self._page()
        elif self.path == "/api/status":
            self._json(status_payload(cfg))
        elif self.path == "/api/config_view":
            self._json({k: cfg[k] for k in
                        ("cc_url", "cc_key", "cc_password", "proxy",
                         "workers", "interval")})
        elif self.path.startswith("/api/job/"):
            job = JOBS.get(self.path.split("/")[3].split("/")[0])
            if not job:
                return self._json({"error": "no such job"}, 404)
            self._json(job_payload(job))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        global_cfg = load_config()
        n = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return self._json({"error": "bad json"}, 400)
        if self.path == "/api/config":
            for k in ("cc_url", "cc_key", "cc_password", "proxy"):
                if k in payload:
                    global_cfg[k] = str(payload.get(k) or "").strip()
            for k in ("workers", "interval"):
                if k in payload:
                    try:
                        v = float(payload[k])
                    except (TypeError, ValueError):
                        continue
                    global_cfg[k] = (max(1, min(8, int(v))) if k == "workers"
                                     else max(0.1, min(5.0, v)))
            save_config(global_cfg)
            apply_config(global_cfg)
            try:  # drop stale cookie cache from the old config
                os.remove(fantiajp.CC_CACHE)
            except OSError:
                pass
            return self._json({"ok": True})
        if self.path == "/api/scrape":
            items = [str(x) for x in (payload.get("items") or [])][:MAX_ITEMS]
            apply_config(global_cfg)
            job = start_job(items, global_cfg)
            return self._json({"job_id": job["id"], "total": job["total"],
                               "dups": job["dups"]})
        if self.path.startswith("/api/job/"):
            jid = self.path.split("/")[3].split("/")[0]
            job = JOBS.get(jid)
            if not job:
                return self._json({"error": "no such job"}, 404)
            if self.path.endswith("/cancel"):
                job["cancel"] = True
                return self._json({"ok": True})
            return self._json(job_payload(job))
        if self.path == "/api/cache/clear":
            try:
                os.remove(fantiajp.CC_CACHE)
            except OSError:
                pass
            return self._json({"ok": True})
        return self._json({"error": "not found"}, 404)

    def log_message(self, fmt, *args):  # keep the console quiet
        pass


def main():
    cfg = load_config()
    apply_config(cfg)
    host = os.environ.get("WEBUI_HOST", cfg["host"])
    port = int(os.environ.get("WEBUI_PORT", cfg["port"]))
    srv = ThreadingHTTPServer((host, port), Handler)
    print("FantiaJp WebUI -> http://%s:%d  (config: %s)"
          % (host, port, CONFIG_FILE))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
