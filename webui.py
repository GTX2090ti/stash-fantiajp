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
}

_lock = threading.Lock()


def load_config():
    cfg = dict(DEFAULTS)
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
        os.environ["HTTPS_PROXY"] = cfg["proxy"]
        os.environ["HTTP_PROXY"] = cfg["proxy"]
    else:
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("HTTP_PROXY", None)


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
        "proxy": cfg["proxy"],
        "cache": ci,
        "port": cfg["port"],
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


def scrape_batch(items):
    results = []
    sess = fantiajp.new_session()
    csrf = []
    for raw in items:
        pid = resolve_post_id(raw)
        if not pid:
            results.append({"input": raw, "ok": False,
                            "error": "no fantia post id found"})
            continue
        try:
            frag = fantiajp.scrape_id(pid, sess, csrf)
            results.append({"input": raw, "ok": bool(frag),
                            "post_id": pid,
                            "error": None if frag else
                            "scraped empty (not visible / throttled?)",
                            "data": frag})
        except Exception as e:  # noqa: BLE001
            results.append({"input": raw, "ok": False, "post_id": pid,
                            "error": "%s: %s" % (type(e).__name__, e)})
        time.sleep(0.3)  # be gentle with fantia
    return results


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
<div class="row"><button onclick="saveCfg()">保存</button><span class="hint">保存在本目录 webui_config.json（已被 gitignore，不入仓库）。CookieCloud 请求始终绕过代理；代理只用于访问 fantia.jp。</span></div>
</div>
</div>
<script>
let CFG={}, LAST=[];
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
  const s=await (await fetch('/api/status')).json();$('c_proxy').value=s.proxy||'';}
function openSettings(){$('setcard').style.display=$('setcard').style.display==='none'?'block':'none'}
async function saveCfg(){const body={cc_url:$('c_url').value.trim(),cc_key:$('c_key').value.trim(),
  cc_password:$('c_pwd').value,proxy:$('c_proxy').value.trim()};
  await fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  await status();openSettings()}
async function clearCache(){await fetch('/api/cache/clear',{method:'POST'});await status()}
async function go(){
  const items=$('inp').value.split('\\n').map(s=>s.trim()).filter(Boolean);
  if(!items.length)return;
  $('st').innerHTML='<span class="spin"></span> 刮削中…';
  $('out').innerHTML='';
  try{
    const r=await fetch('/api/scrape',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({items})});
    const d=await r.json();LAST=d.results;
    for(const it of d.results)render(it);
    const ok=LAST.filter(x=>x.ok).length;
    $('st').innerHTML='完成：<b class="ok">'+ok+'</b> 成功 / <b class="'+(LAST.length-ok?'err':'ok')+'">'+(LAST.length-ok)+'</b> 失败';
  }catch(e){$('st').innerHTML='<b class="err">'+esc(String(e))+'</b>'}
  await status()}
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
  else{div.innerHTML='<div class="body"><div class="t">失败</div><div class="meta">'+esc(it.input)+'</div><div class="meta">'+esc(it.error||'')+'</div></div>';}
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
                        ("cc_url", "cc_key", "cc_password", "proxy")})
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
            save_config(global_cfg)
            apply_config(global_cfg)
            try:  # drop stale cookie cache from the old config
                os.remove(fantiajp.CC_CACHE)
            except OSError:
                pass
            return self._json({"ok": True})
        if self.path == "/api/scrape":
            items = [str(x) for x in (payload.get("items") or [])][:200]
            with _lock:  # serialize batches; shared session & cache
                apply_config(global_cfg)
                return self._json({"results": scrape_batch(items)})
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
