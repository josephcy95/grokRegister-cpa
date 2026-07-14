<div align="center">

[![Grok Register — 注册即入库 CLIProxyAPI](assets/banner.png)](https://github.com/Git-creat7/grokRegister-cpa)

批量注册 Grok 账号，注册成功后自动把 OAuth 凭证写入 [CLIProxyAPI (CPA)](https://github.com/router-for-me/CLIProxyAPI)：支持本地 auth 目录热加载，也支持 Management API 远程上传。

<p>
  <a href="https://github.com/Git-creat7/grokRegister-cpa/stargazers"><img src="https://img.shields.io/github/stars/Git-creat7/grokRegister-cpa?style=flat&logo=github" alt="GitHub stars"></a>
  <a href="https://github.com/Git-creat7/grokRegister-cpa/network/members"><img src="https://img.shields.io/github/forks/Git-creat7/grokRegister-cpa?style=flat&logo=github" alt="GitHub forks"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="License: MIT"></a>
  <img src="https://img.shields.io/badge/Python-3.9%2B-3776AB.svg" alt="Python 3.9+">
  <img src="https://img.shields.io/badge/Interface-GUI%20%2B%20CLI-success.svg" alt="GUI + CLI">
  <img src="https://img.shields.io/badge/Output-CLIProxyAPI-orange.svg" alt="CLIProxyAPI">
</p>

</div>

---

> 仅用于自动化流程研究、测试环境验证和个人学习。请遵守目标网站服务条款、当地法律法规与第三方服务限制。

## 核心流程

```text
打开注册页 → 创建临时邮箱 → 收验证码 → 填资料 / 过人机验证
   → 拿到 SSO cookie → 授权码流程换 OAuth token（带 referrer=grok-build）
   → 本地写入 cpa_auth_dir  和/或  POST 远程 CPA Management API
   → CPA 热加载，立即可用
```

## 功能

- 注册成功后自动入库 CPA（本地目录 / 远程 Management API，可同时开）
- GUI + CLI 两种运行方式（CLI 仍会打开浏览器完成注册页）
- 三浏览器驱动：本地 Chromium（DrissionPage）、[Browser Use Cloud](https://docs.browser-use.com/cloud/browser/stealth) stealth + 住宅代理（Playwright CDP）、[RoxyBrowser](https://github.com/roxybrowser) 指纹浏览器（本地 API create/open + DrissionPage 附着）
- Chromium/Chrome 自动处理 Turnstile（本地 / Roxy 驱动）
- DuckMail / YYDS / Cloudflare 临时邮箱
- 注册后可选开启 NSFW
- 页面卡住重试、验证码失败换邮箱、浏览器重启与内存清理
- CLI：一次 `Ctrl+C` 安全停止，清理阶段不刷 traceback；再按一次强制中断

## 环境要求

- Python 3.9+
- 本地驱动：Google Chrome 或 Chromium
- Browser Use 驱动：`playwright` Python 包 + Browser Use API Key（**无需**本机安装 Chromium）
- Roxy 驱动：本机已安装并登录 [RoxyBrowser](https://www.roxybrowser.com/)，开启本地 API（默认 `http://127.0.0.1:50000`）并填写 API Token
- 可用的 [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)
- 能访问注册页、临时邮箱 API、`auth.x.ai` 的网络（授权码流程换 token 需要）

## 安装

```bash
git clone https://github.com/Git-creat7/grokRegister-cpa.git
cd grokRegister-cpa
pip install -r requirements.txt
# Browser Use 只需 Python 包；远端浏览器由 Browser Use 托管
# uv pip install playwright
cp config.example.json config.json
```

编辑 `config.json` 后运行。

### Windows 一键启动

1. 按 [DEPLOYMENT.md](DEPLOYMENT.md) 用 Python 3.13 创建 `.venv` 并安装依赖
2. 双击 `start-gui.cmd` 开图形界面，或 `start-cli.cmd` 开命令行（输入 `start` 开始）

## 配置

| 配置项 | 说明 |
| --- | --- |
| `cpa_auto_add` | 是否开启 CPA 自动入库 |
| `cpa_auth_dir` | 本地 CPA auth 目录；写入 `xai-<email>.json`，可留空 |
| `cpa_remote_url` | 远程 CPA 地址，如 `http://你的CPA地址:8317` |
| `cpa_management_key` | 远程 CPA 管理密钥（`remote-management.secret-key` 明文） |
| `email_provider` | `duckmail` / `yyds` / `cloudflare` |
| `register_count` | 目标注册数量 |
| `proxy` | 代理；换 token 的 OAuth 请求也走此代理 |
| `enable_nsfw` | 注册后是否尝试开启 NSFW |
| `cloudflare_api_base` | Cloudflare 临时邮箱 API 根地址 |
| `cloudflare_api_key` | 默认匿名模式留空；admin 模式填 `ADMIN_PASSWORD` |
| `cloudflare_auth_mode` | `none` / `bearer` / `x-api-key` / `x-admin-auth` / `query-key` |
| `cloudflare_custom_auth` | Worker 全局密码（`PASSWORDS`），注入 `x-custom-auth` |
| `cloudflare_path_*` | domains / accounts / token / messages 路径 |
| `defaultDomains` | Cloudflare 默认收信域名 |
| `browser_driver` | `local` / `browser_use` / `roxy` |
| `browser_use_api_key` | Browser Use Cloud API Key（也可用环境变量 `BROWSER_USE_API_KEY`） |
| `browser_use_proxy_country` | 代理国家代码，如 `us` / `jp` / `sg` / `de`（两位小写） |
| `browser_use_use_proxy` | `true` 时连接参数带 `proxyCountryCode` |
| `browser_use_profile_id` | 可选；固定 profile 复用 cookies（批量注册建议留空） |
| `browser_use_cdp_base` | 默认 `wss://connect.browser-use.com` |
| `browser_use_timeout_minutes` | 远端会话超时（分钟，默认 15，最大 240） |
| `browser_use_nav_timeout` | Playwright 导航/操作超时（秒） |
| `roxy_api_base` | Roxy 本地 API，默认 `http://127.0.0.1:50000` |
| `roxy_api_token` | Roxy API Token（也可用环境变量 `ROXY_API_TOKEN`） |
| `roxy_workspace_id` | 工作区 ID；留空时启动时自动从 `/browser/workspace` 探测 |
| `roxy_project_id` | 可选项目 ID |
| `roxy_profile_id` | **一号一环境时必须留空**；仅复用固定环境时填写 |
| `roxy_one_profile_per_account` | `true`（默认）：每账号 create 新环境，结束后 close+delete |
| `roxy_delete_profile_after_run` | 一号一环境结束后是否删除 Profile（默认 `true`） |
| `roxy_keep_browser_open` | 调试用；`true` 时不 close/delete |
| `roxy_open_headless` | 是否无头打开 Roxy 窗口 |
| `roxy_default_os` | 创建指纹 OS：`Windows` / `macOS` / `Linux` 等 |
| `roxy_create_use_proxy` | `true` 时把 `config.proxy` 写入 Roxy `proxyInfo` |
| `roxy_load_turnstile_extension` | `true`（默认）：创建/打开环境时尝试注入 `turnstilePatch/` 路径 |
| `roxy_turnstile_extension_path` | 扩展目录绝对路径；留空则用仓库内 `turnstilePatch/` |
| `capmonster_enabled` / `capmonster_api_key` | 可选云解 Turnstile；关掉或留空 key 则走本地点击 + 扩展 |
| `turnstile_warmup_seconds` | 填完资料后预热秒数（默认 `2`），给 iframe / once-click 扩展时间 |
| `turnstile_force_enable_submit` | token 已就绪但 Complete 仍 disabled 时强制启用（默认 `true`） |

### 并发模型（顺序，非并行）

注册是 **顺序 / 单账号流水线**，不是多浏览器并行：

- GUI / CLI 在 **一个** 后台线程里跑 `for i in range(count)`（`register_count`）
- 同一时刻只开 **一个** 浏览器会话（local / Roxy profile / Browser Use session）
- `register_count: N` 表示 **连续做 N 个账号**，前一个成功或失败后再开下一个
- Roxy 一号一环境：账号 i create → 注册 → close+delete → 账号 i+1 再 create
- **没有** 多 worker / 多 profile 并行注册；`parallel_sso_convert.py` 只对已有 SSO 做 CPA 转换并行，与注册无关

若要更高吞吐，只能开多个进程/机器并自备多出口 IP（本仓库未内置）。

### Browser Use Cloud（推荐，规避本机 Chromium / 指纹问题）

与 [turb-gpt-free-register](https://github.com/josephcy95/turb-gpt-free-register) 相同接入方式：Playwright `connect_over_cdp` 到 Browser Use stealth Chromium。

```json
{
  "browser_driver": "browser_use",
  "browser_use_api_key": "bu_...",
  "browser_use_proxy_country": "us",
  "browser_use_use_proxy": true,
  "browser_use_timeout_minutes": 15
}
```

CLI 示例：

```bash
# 使用 Browser Use + 美国出口
python grok_register_ttk.py cli --driver browser_use --country us --count 1

# API Key 也可环境变量
export BROWSER_USE_API_KEY=bu_...
python grok_register_ttk.py cli --driver browser_use --country jp

# 回到本机 Chromium
python grok_register_ttk.py cli --driver local --count 1
```

GUI：配置区选择 **浏览器驱动** = `browser_use`，填写 API Key / 国家代码后开始。

文档：
- https://docs.browser-use.com/cloud/browser/stealth
- https://docs.browser-use.com/cloud/browser/playwright-puppeteer-selenium

### RoxyBrowser（本地指纹浏览器，一号一环境）

与 turb-gpt-free-register 相同思路：调用 Roxy 本地 API **创建 → 打开 → 附着 debuggerAddress → 注册 → 关闭并删除**。

前提：
1. 本机已安装并登录 RoxyBrowser
2. 设置里开启 API，地址一般为 `http://127.0.0.1:50000`
3. 复制 API Token

```json
{
  "browser_driver": "roxy",
  "roxy_api_base": "http://127.0.0.1:50000",
  "roxy_api_token": "你的token",
  "roxy_workspace_id": "",
  "roxy_one_profile_per_account": true,
  "roxy_delete_profile_after_run": true,
  "roxy_default_os": "Windows"
}
```

- `roxy_workspace_id` 可留空：启动时会请求 `/browser/workspace` 自动选第一个工作区/项目
- **一号一环境**：每个账号强制 `create` 新 Profile，`stop_browser` / 重启浏览器时 `close` + `delete`；不要填 `roxy_profile_id`
- 需要给 Roxy 环境挂代理时：设置 `proxy` + `"roxy_create_use_proxy": true`
- **Turnstile 扩展**：默认 `roxy_load_turnstile_extension: true`，创建时会把本仓库 `turnstilePatch/` 路径写入 create/open 的 `extensionPath` / `--load-extension=...`（**不同 Roxy 版本字段可能被忽略**）。若 GUI 里看不到扩展：
  1. 日志应有 `[Roxy] turnstilePatch path=...`
  2. 临时 `roxy_delete_profile_after_run: false` 打开环境，在 Roxy 里确认扩展
  3. 若 API 不支持路径注入：在 Roxy GUI **手动 Load unpacked** `turnstilePatch/` 做成模板，或把绝对路径写到 `roxy_turnstile_extension_path` / `roxy_profile_create_payload`
  4. 详见 [`turnstilePatch/README.md`](turnstilePatch/README.md)
- 关 CapMonster 时仍可用扩展 once-click + `turnstile_warmup_seconds` + force-enable；managed CF 仍强烈依赖 IP

CLI：

```bash
export ROXY_API_TOKEN=你的token
python grok_register_ttk.py cli --driver roxy --count 1

# 或显式传参
python grok_register_ttk.py cli --driver roxy --roxy-token 你的token --roxy-base http://127.0.0.1:50000

# 不用 CapMonster（纯本地点击 + 扩展）
python grok_register_ttk.py cli --driver roxy --no-capmonster --count 1
```

GUI：驱动选 `roxy`，填 **Roxy Token**（及可选 Workspace ID），勾选「一号一环境」。CapMonster 可取消「云解 Turnstile」。

说明：Roxy 提供真实 antidetect 指纹与独立 cookie 环境，**不保证**自动通过 Cloudflare Turnstile；脏 IP 上请开 CapMonster。

### Cloudflare 邮箱（默认匿名）

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://你的-worker-api-域名",
  "cloudflare_api_key": "",
  "cloudflare_auth_mode": "none",
  "cloudflare_path_domains": "/api/domains",
  "cloudflare_path_accounts": "/api/new_address",
  "cloudflare_path_token": "/api/token",
  "cloudflare_path_messages": "/api/mails",
  "defaultDomains": "你的收信域名.com"
}
```

匿名创建失败（例如 Turnstile）时可改 admin 创建：

```json
{
  "cloudflare_api_key": "你的 ADMIN_PASSWORD",
  "cloudflare_auth_mode": "x-admin-auth",
  "cloudflare_path_accounts": "/admin/new_address"
}
```

调试创建接口：

```bash
python cf_mail_debug.py \
  --api-base "https://你的-worker-api-域名" \
  --auth-mode x-admin-auth \
  --api-key "你的 ADMIN_PASSWORD" \
  --create-path /admin/new_address \
  --domain "你的收信域名.com"
```

Worker 若配置了全局 `PASSWORDS`，再加：

```json
{ "cloudflare_custom_auth": "你的全局访问密码" }
```

## CPA 自动入库

SSO 不是 CPA 凭据。程序会：

1. 用 SSO 走授权码流程（`referrer=grok-build`）向 `auth.x.ai` 换 `access_token` / `refresh_token`
2. 组装 `type=xai` 扁平 auth（`cli-chat-proxy.grok.com`）
3. 本地：`cpa_auth_dir` → `xai-<email>.json`（CPA 热加载）
4. 远程：`POST {cpa_remote_url}/v0/management/auth-files?name=...`（需管理密钥）

### 本地目录

```json
{
  "cpa_auto_add": true,
  "cpa_auth_dir": "你的CPA auth目录"
}
```

`cpa_auth_dir` 填 CPA 实际监听的 auth 目录路径即可。

### 远程 Management API

```json
{
  "cpa_auto_add": true,
  "cpa_auth_dir": "",
  "cpa_remote_url": "http://你的CPA地址:8317",
  "cpa_management_key": "你的管理密钥明文"
}
```

要求 CPA：`remote-management.allow-remote` 按访问方式配置；密钥为配置里的明文（启动后配置文件可能被写成 bcrypt，上传仍用明文）。

本地与远程可同时开启。日志前缀：`[CPA]`。

### 独立转换

已有 SSO 时可脱离注册流程：

```bash
# 写本地目录
python sso_to_auth_json.py --sso sso_list.txt --cpa-auth-dir /path/to/auths

# 上传远程 CPA
python sso_to_auth_json.py --sso sso_list.txt \
  --cpa-remote-url http://你的CPA地址:8317 \
  --cpa-management-key '你的管理密钥'

# 单个 cookie + 代理
python sso_to_auth_json.py --sso-cookie 'eyJ...' \
  --cpa-auth-dir ./auths \
  --proxy http://127.0.0.1:7890
```

`sso_list.txt`：一行一个 SSO，或 `邮箱----密码----sso`。

### 为什么必须用授权码流程

这是本项目区别于普通 SSO→token 脚本的关键，踩过坑后固化下来：

- **SSO 不能直接喂给 CPA。** CPA 走 OAuth，需要 `access_token` / `refresh_token`，SSO cookie 只是换 token 的入场券。
- **必须带 `referrer=grok-build`。** xAI 后端要求 access_token 携带 `referrer=grok-build` claim，否则 grok build 通道（`cli-chat-proxy.grok.com`）拒绝，调用 chat 时报 `permission-denied / Access to the chat endpoint is denied`。早期用 device flow 换的 token **不带**这个 claim，会全部失效。
- **解法：授权码流程（Authorization Code + PKCE）。** 在 `/oauth2/authorize` 和 consent 提交两处注入 `referrer=grok-build`，换出的 token 才带此 claim。程序换完会自动校验，日志显示 `access_token 已带 referrer=grok-build`。
- **base_url 必须是 `cli-chat-proxy.grok.com/v1`。** 写入的 auth 记录 `base_url` 指向 grok build 免费通道；若为空，CPA 会回退到计费通道 `api.x.ai/v1`，同样触发 `permission-denied`。

如果 CPA 里已有旧的失效号（`base_url=api.x.ai/v1` 或 `referrer=None`），用本节的独立转换脚本以相同邮箱重新生成一遍覆盖即可（文件名按 `xai-<email>.json` 命名，会原地覆盖）。

## 运行

### CLI

```bash
python grok_register_ttk.py cli
```

提示后输入 `start`。  
`Ctrl+C` 一次：当前账号收尾后停止；清理浏览器时不会因二次中断刷 traceback。再按一次强制退出。

### GUI

```bash
python grok_register_ttk.py
```

可在界面里改：邮箱服务商、代理、Cloudflare（API Base / 鉴权 / 收信域名 / 全局密码）、CPA 开关、auth 目录、远程地址与管理密钥。点击「开始注册」时会写回 `config.json`。

## 输出文件

| 文件 | 内容 |
| --- | --- |
| `accounts_*.txt` | 邮箱、密码、SSO |
| `mail_credentials.txt` | 临时邮箱凭证 |

均含敏感信息，已在 `.gitignore` 中忽略。`config.json` 也不提交，请用 `config.example.json` 复制。

## 稳定性

- 每账号结束后重启浏览器
- 每成功 5 个账号做一次内存清理
- 邮箱提交后确认页面前进，避免空等验证码
- 未收到验证码时换邮箱重试
- 最终页卡住时重试当前账号

## 常见问题

**CPA 没出现新账号**  
检查 `cpa_auto_add`、`cpa_auth_dir` 或 `cpa_remote_url` + `cpa_management_key`；看 `[CPA]` 日志是否换 token / 上传成功；本机/服务器能否访问 `auth.x.ai`。

**远程上传失败**  
确认 CPA 管理 API 已启用、密钥明文正确；远程访问需 `allow-remote: true`。可用：

```bash
curl -H "Authorization: Bearer <管理密钥>" \
  http://你的CPA地址:8317/v0/management/auth-files
```

`cpa_remote_url` 填 CPA 实例根地址，不要附带 OpenAI 兼容接口的 `/v1`。程序会自动追加 `/v0/management/auth-files`。

**创建 Cloudflare 邮箱时 curl 超时**

如果当前网络需要代理访问 `workers.dev`，请在 GUI 的“代理”字段或 `config.json` 的 `proxy` 中显式填写代理地址。不要只依赖终端的 `HTTP_PROXY` / `HTTPS_PROXY`，从桌面启动 GUI 时可能不会继承这些环境变量。

**开启 NSFW 时返回 403**

设置出生日期可能被 `grok.com` 的 Cloudflare 防护拦截。该步骤失败不会影响账号保存和 CPA 入库；不需要敏感内容时可关闭“注册后开启 NSFW”。

**CLI 为什么还开浏览器**  
CLI 只是不启动 Tk；注册页、Turnstile、SSO 仍依赖真实浏览器。

**NSFW 失败**  
常见为 Cloudflare 拦截。账号仍会保存并入库 CPA。

**国内服务器调模型超时**  
入库成功只说明凭证到了 CPA；调用上游 `cli-chat-proxy.grok.com` 还需服务器出网可达（或配置 CPA `proxy-url`）。

**CPA 返回 `503 auth_unavailable: no auth available`**  
不是网络超时，而是 CPA 当前没有可用的 xAI auth。检查：auth 是否写入并被热加载、token 是否带 `referrer=grok-build`、账号是否 403 权限拒绝或 429 免费额度耗尽。free 号走 `cli-chat-proxy` 的 build 通道，额度与权限由上游控制，可能抖动。

**chat 报 `permission-denied` / Access to the chat endpoint is denied**  
token 缺 `referrer=grok-build`，或 `base_url` 误指向 `api.x.ai`。用本仓库授权码流程重转覆盖对应 `xai-<email>.json`。

## 目录结构

```text
.
├── grok_register_ttk.py      # 主程序（GUI / CLI + CPA 入库）
├── sso_to_auth_json.py       # SSO → CPA 转换（可独立运行）
├── cf_mail_debug.py          # Cloudflare 邮箱调试
├── config.example.json
├── requirements.txt
├── start-gui.cmd             # Windows 启动 GUI
├── start-cli.cmd             # Windows 启动 CLI
├── DEPLOYMENT.md             # 本机 / Windows 部署
├── tests/
└── assets/banner.png
```

## Star History

<a href="https://www.star-history.com/?type=date&repos=Git-creat7%2FgrokRegister-cpa">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=Git-creat7/grokRegister-cpa&type=date&theme=dark&legend=top-left&sealed_token=nLCws8QCmosQswlx1hTjASUcz8r72ZEKXOP1C8WmTFqosF65NL66q77qlMIbBZ6Kqic0cOqA5VisinVcERXNFlwMZqx0ET8872ALY3-k8rvCyvNqa-RxzMLV_oOrrAV54D0E6Pfv4WWTmaA6WYQBr2U5dizobLbasNLXpKTnZZJI7-uRL0zomGISzIGq" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=Git-creat7/grokRegister-cpa&type=date&legend=top-left&sealed_token=nLCws8QCmosQswlx1hTjASUcz8r72ZEKXOP1C8WmTFqosF65NL66q77qlMIbBZ6Kqic0cOqA5VisinVcERXNFlwMZqx0ET8872ALY3-k8rvCyvNqa-RxzMLV_oOrrAV54D0E6Pfv4WWTmaA6WYQBr2U5dizobLbasNLXpKTnZZJI7-uRL0zomGISzIGq" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=Git-creat7/grokRegister-cpa&type=date&legend=top-left&sealed_token=nLCws8QCmosQswlx1hTjASUcz8r72ZEKXOP1C8WmTFqosF65NL66q77qlMIbBZ6Kqic0cOqA5VisinVcERXNFlwMZqx0ET8872ALY3-k8rvCyvNqa-RxzMLV_oOrrAV54D0E6Pfv4WWTmaA6WYQBr2U5dizobLbasNLXpKTnZZJI7-uRL0zomGISzIGq" />
 </picture>
</a>

## License

[MIT](LICENSE)

## Acknowledgments

Thanks to [linux.do](https://linux.do) and [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI).
