# FantiaJp (fragment-capable build) — Stash scraper for fantia.jp

A hardened, fragment-capable rewrite of the community [FantiaJp](https://github.com/stashapp/CommunityScrapers/tree/master/scrapers/FantiaJp) scraper for [Stash](https://github.com/stashapp/stash).

**Highlights**

- `sceneByFragment` / `galleryByFragment` — shows up in the **Scrape with…** menu (upstream is URL-only, so it never appears there) and works for batch scraping
- Robust against Fantia's inconsistent post JSON — `thumb`/`title`-like fields are sometimes dicts, not strings, which crashes naive scrapers and surfaces in Stash as `could not unmarshal json from script output: EOF`
- Fails softly: deleted / members-only / throttled posts return an empty result with a diagnostic, never a crashed process
- Quiet by default — diagnostics go to stderr only with `FANTIA_DEBUG=1`
- Optional login via, in priority order: `FANTIA_COOKIE` env → `fantia_cookie.txt` file → self-hosted **CookieCloud** server (fetched live, cached 1 h)

## Install

1. Copy `fantiajp.py` + `FantiaJp.yml` into your Stash scrapers path (default `~/.stash/scrapers/`), e.g. `scrapers/community/FantiaJp/`.
2. In Stash: **Scrape with… → Reload scrapers**.

Python `requests` is required inside the environment that runs Stash (`pip install requests`). Everything else is stdlib — the CookieCloud client ships a pure-Python AES fallback, no extra crypto packages needed.

> ⚠️ If you installed the upstream FantiaJp through Stash's package manager, installing this over it may be reverted the next time Stash refreshes package sources. Delete the installed package first, or maintain this copy in a location not managed by `installPackages` (the background job re-extracts the official zip and silently overwrites manual edits).

## Login / cookie setup

Fantia hides members-only posts; `/api/v1/posts/<id>` answers HTTP 422 for anything the session may not see (deleted, members-only, or not logged in — Fantia does not distinguish). The scraper tries, in order:

1. `FANTIA_COOKIE` env var — a raw cookie string, e.g. `_session_id=...`
2. `fantia_cookie.txt` next to the script (or in the Stash config dir) — raw cookie string or Netscape `cookies.txt` format
3. **CookieCloud** (self-hosted) — set these env vars where the Stash server process runs (docker-compose `environment:`, systemd `Environment=`, etc.):

   | Variable | Meaning |
   |---|---|
   | `CC_COOKIECLOUD_URL` | Base URL of your CookieCloud instance |
   | `CC_COOKIECLOUD_KEY` | Device key (UUID) |
   | `CC_COOKIECLOUD_PASSWORD` | Sync password |
   | `CC_TIMEOUT` / `CC_TTL` | Optional: fetch timeout (s) and cache lifetime (s, default 3600) |

   Log into fantia.jp in a browser covered by CookieCloud's browser extension, sync once, and the scraper picks the cookie up automatically.

Without any cookie only genuinely public posts will scrape.

## Batch scraping

- **Scenes/Galleries**: multi-select in the grid → **Scrape with… → FantiaJp**.
- Post IDs are resolved from the fragment in this order: existing `fantia.jp/posts/<id>` URL → previously scraped `FANTIA-<id>` code → digits in the filename. Scenes without any of these are skipped silently.
- Keep batches moderate (Fantia throttles aggressive request rates). Each scene costs ~1–2 s.

## Troubleshooting

| Symptom | Meaning |
|---|---|
| `could not unmarshal json from script output: EOF` | The script crashed before printing JSON — check the traceback in **Settings → Logs** (Debug level). Fixed here for dict-shaped JSON; if you see it again, the log tells you exactly where. |
| `HTTP 422 ... not visible to this session` | Cookie expired / not logged in / post deleted. Re-sync your cookie. |
| `HTTP 403` | Invalid cookie or IP throttled — wait, or refresh the login. |
| Scene silently not scraped | No post ID found in URL/code/filename. |

## License

AGPL-3.0 — same as the upstream [CommunityScrapers](https://github.com/stashapp/CommunityScrapers) repository this is derived from. See [LICENSE](LICENSE).

Scraping fantia.jp is for personal, archival use of content you have access to. Respect Fantia's terms of service and the creators whose work you are indexing.
