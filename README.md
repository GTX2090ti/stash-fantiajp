# FantiaJp (fragment-capable build)

English | [简体中文](README.zh-CN.md)

A hardened, fragment-capable rewrite of the community [FantiaJp](https://github.com/stashapp/CommunityScrapers/tree/master/scrapers/FantiaJp) scraper for [Stash](https://github.com/stashapp/stash), plus an optional zero-dependency WebUI for batch scraping.

## Contents

- [What's different from upstream](#whats-different-from-upstream)
- [Requirements](#requirements)
- [Install](#install)
- [Login / cookie setup](#login--cookie-setup)
- [Usage in Stash](#usage-in-stash)
- [WebUI](#webui)
- [Configuration reference](#configuration-reference)
- [File layout](#file-layout)
- [Troubleshooting](#troubleshooting)
- [Changelog](#changelog)
- [License](#license)

## What's different from upstream

| | Upstream FantiaJp | This build |
|---|---|---|
| `Scrape with…` menu | ✗ not listed (URL-only) | ✓ `sceneByFragment` / `galleryByFragment` |
| Batch scraping | ✗ | ✓ (fragment → post/product ID resolution) |
| Products (`/products/<id>`) | ✗ | ✓ title/cover/date/creator/tags/details |
| Galleries | URL only | fragment entry included |
| Malformed post JSON | crashes (Stash shows `EOF` error) | tolerated — dict-shaped `thumb`/`title` fields are drilled into |
| Login | manual cookie file | env → file → **CookieCloud** (auto, 1 h cache) |
| Failure behavior | crashed process, empty stdout | soft-fail with a diagnostic in the result |
| WebUI | ✗ | ✓ optional, stdlib-only |

## Requirements

- Stash v0.28+ (tested on v0.31)
- Python 3 with `requests` available to whatever environment launches Stash scrapers (`pip install requests`). Everything else is stdlib — the CookieCloud client ships a pure-Python AES fallback, no extra crypto packages.

## Install

1. Copy `fantiajp.py`, `FantiaJp.yml` and `manifest` into your Stash scrapers path, e.g. `~/.stash/scrapers/community/FantiaJp/`.
2. In Stash: **Scrape with… → Reload scrapers**.

> ⚠️ **Package-manager conflict**: if you installed upstream FantiaJp through Stash's package manager, Stash's background `installPackages` job re-extracts the official zip and **silently overwrites manual edits**. Delete the managed package first, or keep this copy in a location not managed by `installPackages`. After installing, verify **Settings → Log** shows no overwrite and that `supported_scrapes` includes `FRAGMENT`.

## Login / cookie setup

Fantia hides members-only posts; `/api/v1/posts/<id>` answers HTTP 422 for anything the session may not see (deleted, members-only, or not logged in — Fantia does not distinguish). The scraper picks up a session cookie from, in priority order:

| # | Source | How |
|---|---|---|
| 1 | `FANTIA_COOKIE` env var | raw cookie string, e.g. `_session_id=...` |
| 2 | `fantia_cookie.txt` | next to the script or in the Stash config dir; raw cookie string or Netscape `cookies.txt` format |
| 3 | **CookieCloud** (self-hosted) | see below; fetched live and cached for 1 h |

CookieCloud setup — set these env vars where the Stash server process runs (docker-compose `environment:`, systemd `Environment=`, etc.), log into fantia.jp in a browser covered by CookieCloud's extension, sync once, and the scraper picks the cookie up automatically:

| Variable | Meaning |
|---|---|
| `CC_COOKIECLOUD_URL` | Base URL of your CookieCloud instance |
| `CC_COOKIECLOUD_KEY` | Device key (UUID) |
| `CC_COOKIECLOUD_PASSWORD` | Sync password |

Without any cookie only genuinely public posts will scrape.

## Usage in Stash

**Single**: open a scene/gallery → **Scrape with… → FantiaJp**.

**Batch**: multi-select scenes or galleries in the grid → **Scrape with… → FantiaJp** → review each preview → apply.

**Identify task**: add FantiaJp as the only source under *Settings → Identify* to avoid unrelated fragment scrapers (duga, ThePornDB, …) firing 404 noise against Fantia file names.

Post and product IDs are resolved from the fragment in this order:

| Priority | Source | Example |
|---|---|---|
| 1 | Existing product URL on the scene | `fantia.jp/products/1035222` |
| 2 | Existing post URL on the scene | `fantia.jp/posts/1180318` |
| 3 | Previously scraped code field | `FANTIA-1180318` / `FANTIA-P1035222` |
| 4 | Digits in the filename/path | `fantia-976153.mp4` (treated as a post) |

Scenes matching none of these are skipped silently.

**Products** (`fantia.jp/products/<id>`, shop items) are supported since 2026-09-14. There is no JSON API for products, so the scraper reads the page's embedded structured data: JSON-LD (`name`, image list, `VideoObject.uploadDate`), the `gtm-json` block (`fanclub_name`, tags) and the `product-description` section (full text). Product scenes get code `FANTIA-P<id>` to stay distinguishable from post ids.

Keep batches moderate (Fantia throttles aggressive request rates); each scene costs ~1–2 s. The CookieCloud cache means the cookie is fetched at most once per hour regardless of batch size.

## WebUI

`webui.py` is an optional local front-end for batch scraping, stdlib-only (no extra pip installs):

```
python webui.py          # then open http://127.0.0.1:8799
```

Features:

- Paste one item per line — post/product URL / numeric ID / `FANTIA-<id>` or `FANTIA-P<id>` filename → batch scrape → result cards (cover, date, tags, performers, details) → export JSON
- **Concurrent batch engine**: 1–8 workers (default 3) with adaptive rate limiting — the request interval doubles on 403/429/network errors (cap 8 s) and decays back after 5 consecutive successes; duplicates are detected and skipped before any request fires
- **Live progress**: results stream in as they complete (no waiting for the whole batch); jobs can be cancelled mid-run
- **CookieCloud & proxy settings are editable in the browser** — no env vars needed. Saved to `webui_config.json` next to the script (gitignored, never leaves the machine) and applied hot
- Cookie cache status + one-click clear

`WEBUI_HOST` / `WEBUI_PORT` env vars override the bind address (default `127.0.0.1:8799`).

Settings precedence (highest first): UI-saved `webui_config.json` → `fantiajp.py` env vars / baked-in defaults. CookieCloud requests always bypass the proxy; the proxy only applies to fantia.jp traffic.

### Docker (bundled image)

This repository ships a `Dockerfile` and `docker-compose.yml` — the image contains `webui.py` + `fantiajp.py`, its only dependency is `requests` (use `--build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple` for faster builds in China):

```bash
git clone https://github.com/GTX2090ti/stash-fantiajp.git
cd stash-fantiajp

# option A: compose (edit CC_COOKIECLOUD_* in docker-compose.yml first)
docker compose up -d

# option B: plain docker
docker build -t fantia-webui:latest .
docker run -d --name fantia-webui --restart unless-stopped -p 8799:8799 \
  -e CC_COOKIECLOUD_URL=http://your-cookiecloud:8188 \
  -e CC_COOKIECLOUD_KEY=<your-key-uuid> \
  -e CC_COOKIECLOUD_PASSWORD=<your-password> \
  -v /path/to/data:/data \
  fantia-webui:latest
```

Then open `http://<host>:8799`. Notes:

- `/data` (mount it!) holds `webui_config.json` and the CookieCloud cookie cache, so settings and the cached cookie survive container recreation (`WEBUI_CONFIG_DIR` / `CC_CACHE_DIR` control the paths).
- CookieCloud settings can also be entered later in the WebUI settings page — env vars just pre-fill the defaults.
- A healthcheck pings `/api/status` every minute.

### Run it next to your Stash (NAS / shared scraper dir)

The WebUI can share the scraper directory with your Stash container — it then reuses the same `fantiajp.py`, cookie cache and baked-in defaults with zero extra configuration:

```bash
mkdir -p /vol2/docker/fantia-webui && cd /vol2/docker/fantia-webui
cat > Dockerfile <<'EOF'
FROM python:3.12-alpine
WORKDIR /app
ARG PROXY
ENV HTTPS_PROXY=${PROXY} HTTP_PROXY=${PROXY}
RUN pip install --no-cache-dir requests
ENV WEBUI_HOST=0.0.0.0 WEBUI_PORT=8799
CMD ["python3", "webui.py"]
EOF
docker build --build-arg PROXY=http://your-proxy:7890 -t fantia-webui:latest .

docker run -d --name fantia-webui --restart unless-stopped --network host \
  -v /path/to/stash/scrapers/community/FantiaJp:/app \
  -e HTTPS_PROXY=http://your-proxy:7890 -e HTTP_PROXY=http://your-proxy:7890 \
  fantia-webui:latest
```

Then open `http://<nas-ip>:8799`. Notes:

- `--network host` exposes 8799 on the LAN directly; keep the NAS off the public internet.
- CookieCloud settings left empty in the UI fall back to whatever `fantiajp.py` itself carries, so a private build keeps working with zero UI configuration.
- Settings saved in the UI land in `webui_config.json` inside the mounted scraper dir.
- **Updating**: replace `webui.py` in the mounted dir → `docker restart fantia-webui` (no image rebuild needed).

## Configuration reference

All env vars, read where the respective process runs:

| Variable | Applies to | Default | Meaning |
|---|---|---|---|
| `FANTIA_COOKIE` | scraper | — | Raw session cookie (highest cookie priority) |
| `FANTIA_DEBUG` | scraper | off | `1` = stderr diagnostics |
| `FANTIA_RETRIES` | scraper | `2` | Extra attempts for transient failures (network / 429 / 5xx; 403 gets one) |
| `FANTIA_BACKOFF` | scraper | `1.0` | Base delay (s) between retries, exponential |
| `CC_COOKIECLOUD_URL` | scraper | — | CookieCloud base URL |
| `CC_COOKIECLOUD_KEY` | scraper | — | Device key (UUID) |
| `CC_COOKIECLOUD_PASSWORD` | scraper | — | Sync password |
| `CC_TIMEOUT` | scraper | `8` | CookieCloud fetch timeout (s) |
| `CC_TTL` | scraper | `3600` | Cookie cache lifetime (s) |
| `HTTPS_PROXY` / `HTTP_PROXY` | both | — | Proxy for fantia.jp (CookieCloud always bypasses it) |
| `WEBUI_HOST` / `WEBUI_PORT` | WebUI | `127.0.0.1` / `8799` | Bind address |
| `WEBUI_CONFIG_DIR` | WebUI | script dir | Where `webui_config.json` is stored (Docker: `/data`) |
| `CC_CACHE_DIR` | scraper | script dir | Where the CookieCloud cookie cache is written (Docker: `/data`) |

## File layout

| File | Purpose |
|---|---|
| `fantiajp.py` | The scraper (Stash calls it as a script) |
| `FantiaJp.yml` | Scraper definition — fragment entries included |
| `manifest` | Package-manager metadata |
| `webui.py` | Optional WebUI server |
| `Dockerfile` / `docker-compose.yml` | WebUI container image (only dep: requests) |
| `index.yml` / `FantiaJp.zip` | Stash package-source files for install-via-URL |
| `.fantia_cookiecc.json` | Cookie cache created at runtime (gitignored — contains real cookies) |
| `fantia_cookie.txt` | Optional manual cookie file (gitignored) |
| `webui_config.json` | WebUI settings created at runtime (gitignored) |

## Troubleshooting

| Symptom | Meaning | Fix |
|---|---|---|
| `could not unmarshal json from script output: EOF` | The script crashed before printing JSON | Check the traceback in **Settings → Logs** (set log level to Debug). Dict-shaped JSON is handled here; if you still see it, the log names the exact line |
| `HTTP 422 ... not visible to this session` | Cookie expired / not logged in / post deleted — Fantia does not distinguish | Re-sync your cookie (CookieCloud sync or fresh `fantia_cookie.txt`) |
| `HTTP 403` | Invalid cookie or IP throttled | Wait, or refresh the login |
| Scene silently not scraped | No post ID found in URL/code/filename | Add a `fantia.jp/posts/<id>` URL or rename the file |
| `duga.jp ... 404` in logs during Identify | Unrelated built-in scrapers probing the fragment | Limit Identify sources to FantiaJp (see [Usage](#usage-in-stash)) |
| Overwritten back to upstream behavior | Stash `installPackages` re-extracted the official zip | See [Install](#install) warning |

## Changelog

- **2026-09-15** — Docker packaging: `Dockerfile` + `docker-compose.yml` for the WebUI (minimal image, persistent `/data` volume via new `WEBUI_CONFIG_DIR`/`CC_CACHE_DIR` env vars, healthcheck, pip index build-arg); Stash package source (`index.yml` + `FantiaJp.zip`) for install-via-URL; `CC_CACHE_DIR` env var added to the scraper.

- **2026-09-14** — Product page support: `fantia.jp/products/<id>` URLs now scrape (title, cover, date, creator, tags, full description via JSON-LD + `gtm-json` + description section); product scenes carry code `FANTIA-P<id>`; `sceneByURL` accepts products; WebUI resolves product URLs/codes.

- **2026-09-13 (2)** — Batch scraping optimization: retry with exponential backoff for transient failures in the scraper core; WebUI batch engine rewritten — concurrent workers, adaptive rate limiting, live progress + cancel, duplicate-line dedup.
- **2026-09-13** — WebUI added (batch scrape, in-browser CookieCloud settings, JSON export); NAS Docker deployment guide; WebUI inherits scraper's baked-in defaults when UI config is empty.
- **2026-09-12** — Fixed crash on dict-shaped `thumb`/`title` fields (the `EOF` bug); `_s()` tolerant getter across all string fields; regression suite grown to 92 assertions.
- **2026-09-11** — Initial fragment-capable build: `sceneByFragment`/`galleryByFragment`, CookieCloud client with AES fallback and 1 h cache.

## License

AGPL-3.0 — same as the upstream [CommunityScrapers](https://github.com/stashapp/CommunityScrapers) repository this is derived from. See [LICENSE](LICENSE).

Scraping fantia.jp is for personal, archival use of content you have access to. Respect Fantia's terms of service and the creators whose work you are indexing.
