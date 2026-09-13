# FantiaJp（fragment 增强版）

[English](README.md) | 简体中文

社区版 [FantiaJp](https://github.com/stashapp/CommunityScrapers/tree/master/scrapers/FantiaJp) 刮削器的强化重写版，适用于 [Stash](https://github.com/stashapp/stash)，并附带一个可选的零依赖批量刮削 WebUI。

## 目录

- [与上游版本的差异](#与上游版本的差异)
- [环境要求](#环境要求)
- [安装](#安装)
- [登录 / Cookie 配置](#登录--cookie-配置)
- [在 Stash 中使用](#在-stash-中使用)
- [WebUI](#webui)
- [配置参考](#配置参考)
- [文件清单](#文件清单)
- [故障排查](#故障排查)
- [更新日志](#更新日志)
- [许可](#许可)

## 与上游版本的差异

| | 上游 FantiaJp | 本版本 |
|---|---|---|
| `Scrape with…` 菜单 | ✗ 不显示（仅支持 URL） | ✓ 含 `sceneByFragment` / `galleryByFragment` |
| 批量刮削 | ✗ | ✓（fragment → 帖子 ID 解析） |
| Gallery（图库） | 仅 URL | 含 fragment 入口 |
| 畸形帖子 JSON | 崩溃（Stash 报 `EOF` 错误） | 容错处理——dict 形状的 `thumb`/`title` 字段自动下钻取值 |
| 登录方式 | 手动 cookie 文件 | 环境变量 → 文件 → **CookieCloud**（自动拉取，缓存 1 小时） |
| 失败行为 | 进程崩溃、stdout 为空 | 软失败，结果中附带诊断信息 |
| WebUI | ✗ | ✓ 可选，纯标准库实现 |

## 环境要求

- Stash v0.28+（在 v0.31 上测试通过）
- 运行 Stash 刮削器的环境需有 Python 3 和 `requests`（`pip install requests`）。其余均为标准库——CookieCloud 客户端内置纯 Python AES 实现，无需额外加密库。

## 安装

1. 把 `fantiajp.py`、`FantiaJp.yml` 和 `manifest` 复制到 Stash 的刮削器目录，例如 `~/.stash/scrapers/community/FantiaJp/`。
2. 在 Stash 中：**Scrape with… → Reload scrapers**。

> ⚠️ **与包管理器的冲突**：如果你曾通过 Stash 的包管理器安装过上游 FantiaJp，后台的 `installPackages` 任务会重新解压官方 zip 并**静默覆盖手动修改**。请先删除包管理器安装的那份，或把本版本放在不受 `installPackages` 管理的位置。安装后请检查 **Settings → Log** 无覆盖记录，且 `supported_scrapes` 包含 `FRAGMENT`。

## 登录 / Cookie 配置

Fantia 的会员专帖需要登录才能查看；`/api/v1/posts/<id>` 对当前会话无权查看的内容（已删除、会员专帖、未登录——Fantia 不做区分）一律返回 HTTP 422。刮削器按以下优先级获取会话 cookie：

| 优先级 | 来源 | 说明 |
|---|---|---|
| 1 | `FANTIA_COOKIE` 环境变量 | 原始 cookie 字符串，如 `_session_id=...` |
| 2 | `fantia_cookie.txt` | 脚本同目录或 Stash 配置目录；支持原始 cookie 字符串或 Netscape `cookies.txt` 格式 |
| 3 | **CookieCloud**（自建） | 见下文；实时拉取，缓存 1 小时 |

CookieCloud 配置——在运行 Stash 服务进程的位置设置这些环境变量（docker-compose 的 `environment:`、systemd 的 `Environment=` 等），然后用被 CookieCloud 浏览器插件覆盖的浏览器登录一次 fantia.jp 并同步，刮削器即可自动拿到 cookie：

| 变量 | 含义 |
|---|---|
| `CC_COOKIECLOUD_URL` | CookieCloud 实例的基础地址 |
| `CC_COOKIECLOUD_KEY` | 设备密钥（UUID） |
| `CC_COOKIECLOUD_PASSWORD` | 同步密码 |

不配置任何 cookie 时，只能刮取真正公开的帖子。

## 在 Stash 中使用

**单个**：打开场景/图库 → **Scrape with… → FantiaJp**。

**批量**：在网格中多选场景或图库 → **Scrape with… → FantiaJp** → 逐个确认预览 → 应用。

**Identify 任务**：在 *Settings → Identify* 中把 FantiaJp 设为唯一来源，避免其他支持 fragment 的刮削器（duga、ThePornDB 等）拿着 Fantia 文件名去查询产生 404 噪音日志。

fragment 中的帖子 ID 按以下优先级解析：

| 优先级 | 来源 | 示例 |
|---|---|---|
| 1 | 场景已有的 URL | `fantia.jp/posts/1180318` |
| 2 | 之前刮过的 code 字段 | `FANTIA-1180318` |
| 3 | 文件名/路径中的数字 | `fantia-976153.mp4` |

三者都不匹配的场景会被静默跳过。注意 `fantia.jp/products/<id>`（商品页）**不是**帖子，不会刮出数据——这是预期行为。

批量请适度（Fantia 会对高频请求限流）；每个场景耗时约 1–2 秒。CookieCloud 有 1 小时缓存，无论批量多大，cookie 至多每小时拉取一次。

## WebUI

`webui.py` 是一个可选的本地批量刮削前端，纯标准库实现（无需额外 pip 安装）：

```
python webui.py          # 然后打开 http://127.0.0.1:8799
```

功能：

- 每行贴一条——帖子 URL / 纯数字 ID / `FANTIA-<id>` 文件名 → 批量刮削 → 结果卡片（封面、日期、标签、表演者、详情）→ 导出 JSON
- **并发批量引擎**：1–8 个并发线程（默认 3），自适应限速——遇到 403/429/网络错误时请求间隔自动翻倍（上限 8 秒），连续成功 5 次后逐步回落；重复行在发出任何请求前就被识别并跳过
- **实时进度**：结果边刮边显示（无需等整批结束）；任务可中途取消
- **CookieCloud 与代理设置可直接在网页里修改**——不依赖环境变量。保存在脚本同目录的 `webui_config.json`（已 gitignore，不会离开本机），保存即热生效
- Cookie 缓存状态展示 + 一键清除

`WEBUI_HOST` / `WEBUI_PORT` 环境变量可覆盖监听地址（默认 `127.0.0.1:8799`）。

设置优先级（从高到低）：网页保存的 `webui_config.json` → `fantiajp.py` 的环境变量 / 内置默认值。CookieCloud 请求始终绕过代理；代理仅作用于 fantia.jp 的流量。

### 与 Stash 同机部署（NAS / Docker）

WebUI 可以与 Stash 容器共享刮削器目录——从而复用同一个 `fantiajp.py`、cookie 缓存和内置默认值，零额外配置：

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

然后打开 `http://<nas-ip>:8799`。注意事项：

- `--network host` 会把 8799 端口直接暴露在局域网；请确保 NAS 不暴露在公网。
- 网页里留空的 CookieCloud 设置会回落到 `fantiajp.py` 自带的值，私有构建无需任何网页配置即可工作。
- 网页保存的设置会写入挂载的刮削器目录中的 `webui_config.json`。
- **更新**：替换挂载目录中的 `webui.py` → `docker restart fantia-webui`（无需重建镜像）。

## 配置参考

所有环境变量，需设置在对应进程的运行环境中：

| 变量 | 作用于 | 默认值 | 含义 |
|---|---|---|---|
| `FANTIA_COOKIE` | 刮削器 | — | 原始会话 cookie（cookie 优先级最高） |
| `FANTIA_DEBUG` | 刮削器 | 关闭 | `1` = 输出 stderr 诊断日志 |
| `FANTIA_RETRIES` | 刮削器 | `2` | 瞬时失败（网络 / 429 / 5xx；403 仅一次）的额外重试次数 |
| `FANTIA_BACKOFF` | 刮削器 | `1.0` | 重试基础间隔（秒），指数递增 |
| `CC_COOKIECLOUD_URL` | 刮削器 | — | CookieCloud 基础地址 |
| `CC_COOKIECLOUD_KEY` | 刮削器 | — | 设备密钥（UUID） |
| `CC_COOKIECLOUD_PASSWORD` | 刮削器 | — | 同步密码 |
| `CC_TIMEOUT` | 刮削器 | `8` | CookieCloud 拉取超时（秒） |
| `CC_TTL` | 刮削器 | `3600` | Cookie 缓存有效期（秒） |
| `HTTPS_PROXY` / `HTTP_PROXY` | 两者 | — | fantia.jp 使用的代理（CookieCloud 始终绕过） |
| `WEBUI_HOST` / `WEBUI_PORT` | WebUI | `127.0.0.1` / `8799` | 监听地址 |

## 文件清单

| 文件 | 用途 |
|---|---|
| `fantiajp.py` | 刮削器本体（Stash 以脚本方式调用） |
| `FantiaJp.yml` | 刮削器定义——含 fragment 入口 |
| `manifest` | 包管理器元数据 |
| `webui.py` | 可选的 WebUI 服务 |
| `.fantia_cookiecc.json` | 运行时生成的 cookie 缓存（已 gitignore——含真实 cookie） |
| `fantia_cookie.txt` | 可选的手动 cookie 文件（已 gitignore） |
| `webui_config.json` | 运行时生成的 WebUI 设置（已 gitignore） |

## 故障排查

| 症状 | 含义 | 处理 |
|---|---|---|
| `could not unmarshal json from script output: EOF` | 脚本在打印 JSON 之前崩溃 | 到 **Settings → Logs**（日志级别设为 Debug）查看 traceback。dict 形状的 JSON 已做容错；若仍出现，日志会指出确切位置 |
| `HTTP 422 ... not visible to this session` | cookie 过期 / 未登录 / 帖子已删除——Fantia 不做区分 | 重新同步 cookie（CookieCloud 同步或更新 `fantia_cookie.txt`） |
| `HTTP 403` | cookie 无效或 IP 被限流 | 等待，或重新登录 |
| 场景被静默跳过 | URL/code/文件名中找不到帖子 ID | 补一个 `fantia.jp/posts/<id>` URL 或重命名文件 |
| Identify 时日志出现 `duga.jp ... 404` | 无关的内置刮削器在探测 fragment | 把 Identify 来源限制为 FantiaJp（见[在 Stash 中使用](#在-stash-中使用)） |
| 行为被改回上游原样 | Stash `installPackages` 重新解压了官方 zip | 见[安装](#安装)的警告 |

## 更新日志

- **2026-09-13 (2)** — 批量刮削优化：刮削核心对瞬时失败增加指数退避重试；WebUI 批量引擎重写——并发线程、自适应限速、实时进度 + 取消、重复行去重。
- **2026-09-13** — 新增 WebUI（批量刮削、网页内 CookieCloud 设置、JSON 导出）；NAS Docker 部署指南；WebUI 在网页配置为空时继承刮削器内置默认值。
- **2026-09-12** — 修复 dict 形状 `thumb`/`title` 字段导致的崩溃（即 `EOF` 问题）；全部字符串字段改用 `_s()` 容错取值；回归测试扩至 92 项断言。
- **2026-09-11** — 首个 fragment 增强版：`sceneByFragment`/`galleryByFragment`、带 AES 兜底与 1 小时缓存的 CookieCloud 客户端。

## 许可

AGPL-3.0 —— 与衍生自的上级仓库 [CommunityScrapers](https://github.com/stashapp/CommunityScrapers) 相同。全文见 [LICENSE](LICENSE)。

刮取 fantia.jp 仅用于对你有权访问内容的个人存档用途。请尊重 Fantia 的服务条款以及你所索引内容的创作者。
