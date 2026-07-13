#!/usr/bin/env python3
"""
SSO cookie → ~/.grok/auth.json 格式（纯 HTTP Device Flow）

用法:
  # 单个 / 批量 SSO，写出多个独立 auth 文件（每个可直接 cp 到 ~/.grok/auth.json）
  python3 sso_to_auth_json.py --sso sso_list.txt --out-dir ./auth_out

  # 合并到一个 json（key 带 user_id 后缀，避免覆盖）
  python3 sso_to_auth_json.py --sso sso_list.txt --out auth_merged.json --merge

  # 单行 sso
  python3 sso_to_auth_json.py --sso-cookie 'eyJ...' --out ~/.grok/auth.json
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import random
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from pathlib import Path

from curl_cffi import requests

CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OIDC_ISSUER = "https://auth.x.ai"
AUTH_KEY = f"{OIDC_ISSUER}::{CLIENT_ID}"
SCOPES = (
    "openid profile email offline_access grok-cli:access "
    "api:access conversations:read conversations:write"
)

# --- CLIProxyAPI (CPA) 扁平格式常量 ------------------------------------------
# CPA 的 internal/auth/xai/token.go TokenStorage 读的是扁平字段。
# Build/CLI token（scope 含 grok-cli:access）必须走 cli-chat-proxy.grok.com，
# 不能用默认 api.x.ai/v1（那是计费通道，会 402）。
CPA_TOKEN_ENDPOINT = f"{OIDC_ISSUER}/oauth2/token"
CPA_GROK_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
CPA_GROK_HEADERS = {
    "X-XAI-Token-Auth": "xai-grok-cli",
    "x-grok-client-version": "0.2.93",
    "x-grok-client-identifier": "grok-shell",
}
RETRYABLE_HTTP_STATUS = {408, 425, 429, 500, 502, 503, 504}


def b64url_decode(seg: str) -> bytes:
    seg += "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg)


def decode_jwt_payload(token: str) -> dict:
    try:
        return json.loads(b64url_decode(token.split(".")[1]))
    except Exception:
        return {}


def rfc3339_ns(ts: float | None = None) -> str:
    """2026-07-10T01:00:00.000000000Z"""
    if ts is None:
        ts = time.time()
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + ".000000000Z"


def _urlopen(req, proxy: str = "", timeout: int = 15):
    """urllib 请求，proxy 非空时走代理。"""
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        )
        return opener.open(req, timeout=timeout)
    return urllib.request.urlopen(req, timeout=timeout)


def retry_request(operation: str, request, retries: int, retry_delay: float, log=print):
    """Retry transient transport and upstream errors with bounded exponential backoff."""
    retries = max(0, int(retries))
    for attempt in range(retries + 1):
        try:
            response = request()
            status = getattr(response, "status_code", getattr(response, "code", None))
            if status not in RETRYABLE_HTTP_STATUS or attempt == retries:
                return response
            reason = f"HTTP {status}"
        except urllib.error.HTTPError as exc:
            if exc.code not in RETRYABLE_HTTP_STATUS or attempt == retries:
                raise
            reason = f"HTTP {exc.code}"
        except Exception as exc:
            if attempt == retries:
                raise
            reason = type(exc).__name__

        delay = retry_delay * (2**attempt) + random.uniform(0, min(0.25, retry_delay))
        log(f"  ↻ {operation} {reason}; retry {attempt + 1}/{retries} in {delay:.1f}s")
        time.sleep(delay)


class RelaySession:
    """Route absolute upstream URLs through the header-based Netlify relay."""

    def __init__(self, relay_url: str, sso_cookie: str, proxy: str = "", relay_key: str = ""):
        self.relay_url = relay_url.rstrip("/") + "/"
        self.relay_key = relay_key
        self.session = requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self.upstream_cookies = [
            {"name": "sso", "value": sso_cookie, "domain": ".x.ai", "path": "/"}
        ]

    def _store_cookies(self, response, upstream_url: str) -> None:
        try:
            values = response.headers.get_list("set-cookie")
        except AttributeError:
            values = [response.headers.get("set-cookie", "")]
        default_domain = urllib.parse.urlsplit(upstream_url).hostname or ""
        for value in values:
            parsed = SimpleCookie()
            try:
                parsed.load(value)
            except Exception:
                continue
            for name, morsel in parsed.items():
                domain = (morsel["domain"] or default_domain).lower()
                path = morsel["path"] or "/"
                self.upstream_cookies = [
                    cookie
                    for cookie in self.upstream_cookies
                    if not (cookie["name"] == name and cookie["domain"] == domain and cookie["path"] == path)
                ]
                self.upstream_cookies.append(
                    {"name": name, "value": morsel.value, "domain": domain, "path": path}
                )

    def _cookie_header(self, target) -> str:
        host = (target.hostname or "").lower()
        path = target.path or "/"
        matched = []
        for cookie in self.upstream_cookies:
            domain = cookie["domain"].lstrip(".")
            if host != domain and not host.endswith("." + domain):
                continue
            if not path.startswith(cookie["path"]):
                continue
            matched.append(f'{cookie["name"]}={cookie["value"]}')
        return "; ".join(matched)

    def request(self, method: str, url: str, **kwargs):
        follow_redirects = kwargs.pop("allow_redirects", True)
        redirect_limit = kwargs.pop("_relay_redirect_limit", 10)
        target = urllib.parse.urlsplit(url)
        if target.scheme not in {"http", "https"} or not target.netloc:
            raise ValueError(f"Relay requires an absolute HTTP URL: {url}")

        headers = dict(kwargs.pop("headers", {}) or {})
        headers["x-relay-target"] = f"{target.scheme}://{target.netloc}"
        headers["x-relay-path"] = urllib.parse.urlunsplit(("", "", target.path or "/", target.query, ""))
        if self.relay_key:
            headers["x-relay-key"] = self.relay_key
        cookie_header = self._cookie_header(target)
        if cookie_header:
            headers["Cookie"] = cookie_header

        response = self.session.request(
            method,
            self.relay_url,
            headers=headers,
            allow_redirects=False,
            **kwargs,
        )
        self._store_cookies(response, url)

        location = response.headers.get("location")
        if (
            follow_redirects
            and redirect_limit > 0
            and response.status_code in {301, 302, 303, 307, 308}
            and location
        ):
            next_method = method
            next_kwargs = dict(kwargs)
            if response.status_code in {301, 302, 303} and method.upper() != "GET":
                next_method = "GET"
                next_kwargs.pop("data", None)
                next_kwargs.pop("json", None)
            next_kwargs["_relay_redirect_limit"] = redirect_limit - 1
            return self.request(next_method, urllib.parse.urljoin(url, location), **next_kwargs)

        response.relay_upstream_url = url
        return response

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)


def _response_url(response) -> str:
    return getattr(response, "relay_upstream_url", response.url)


def request_device_code(
    proxy: str = "",
    relay_session: RelaySession | None = None,
    retries: int = 3,
    retry_delay: float = 1.0,
    log=print,
) -> dict | None:
    data = urllib.parse.urlencode({"client_id": CLIENT_ID, "scope": SCOPES}).encode()
    if relay_session:
        try:
            response = retry_request(
                "device/code",
                lambda: relay_session.post(
                    f"{OIDC_ISSUER}/oauth2/device/code",
                    data=data,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    impersonate="chrome",
                    timeout=15,
                ),
                retries,
                retry_delay,
                log,
            )
            if response.status_code >= 400:
                log(f"  ❌ device/code HTTP {response.status_code}")
                return None
            return response.json()
        except Exception as e:
            log(f"  ❌ device/code 网络错误: {e}")
            return None
    try:
        def post_device_code():
            req = urllib.request.Request(
                f"{OIDC_ISSUER}/oauth2/device/code",
                data=data,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            with _urlopen(req, proxy=proxy, timeout=15) as resp:
                return json.loads(resp.read())

        return retry_request("device/code", post_device_code, retries, retry_delay, log)
    except urllib.error.HTTPError as e:
        log(f"  ❌ device/code HTTP {e.code}: {e.read().decode()[:200]}")
        return None


def poll_token(
    device_code: str,
    interval: int,
    expires_in: int,
    timeout: int = 60,
    proxy: str = "",
    relay_session: RelaySession | None = None,
    retries: int = 3,
    retry_delay: float = 1.0,
    log=print,
) -> dict | None:
    deadline = time.time() + min(expires_in, timeout)
    while time.time() < deadline:
        time.sleep(interval)
        data = urllib.parse.urlencode(
            {
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                "client_id": CLIENT_ID,
                "device_code": device_code,
            }
        ).encode()
        if relay_session:
            try:
                response = retry_request(
                    "token",
                    lambda: relay_session.post(
                        f"{OIDC_ISSUER}/oauth2/token",
                        data=data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        impersonate="chrome",
                        timeout=15,
                    ),
                    retries,
                    retry_delay,
                    log,
                )
                if response.status_code < 400:
                    return response.json()
                err = response.json()
                error = err.get("error", "")
            except Exception as e:
                log(f"  ❌ token 网络错误: {e}")
                return None
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            log(f"  ❌ token: {error or f'HTTP {response.status_code}'}")
            return None
        try:
            def post_token():
                req = urllib.request.Request(
                    f"{OIDC_ISSUER}/oauth2/token",
                    data=data,
                    method="POST",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                with _urlopen(req, proxy=proxy, timeout=15) as resp:
                    return json.loads(resp.read())

            return retry_request("token", post_token, retries, retry_delay, log)
        except urllib.error.HTTPError as e:
            err = json.loads(e.read())
            error = err.get("error", "")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            log(f"  ❌ token: {error}")
            return None
    log("  ❌ 轮询超时")
    return None


def sso_to_token(
    sso_cookie: str,
    proxy: str = "",
    relay_url: str = "",
    relay_key: str = "",
    retries: int = 3,
    retry_delay: float = 1.0,
    log=print,
) -> dict | None:
    """SSO cookie → token dict (access/refresh/expires_in)。可选全程走 header relay。"""
    proxies = {"http": proxy, "https": proxy} if proxy else None
    if relay_url:
        s = RelaySession(relay_url, sso_cookie, proxy=proxy, relay_key=relay_key)
    else:
        s = requests.Session()
        if proxies:
            s.proxies = proxies
        s.cookies.set("sso", sso_cookie, domain=".x.ai")

    try:
        r = retry_request(
            "SSO validation",
            lambda: s.get("https://accounts.x.ai/", impersonate="chrome", timeout=15),
            retries,
            retry_delay,
            log,
        )
    except Exception as e:
        log(f"  ❌ 网络错误: {e}")
        return None
    final_url = _response_url(r)
    if r.status_code >= 400:
        log(f"  ❌ sso 验证 HTTP {r.status_code}")
        return None
    if "sign-in" in final_url or "sign-up" in final_url:
        log("  ❌ sso 无效")
        return None
    log("  ✅ sso 有效")

    log("  🔑 Device Flow...")
    dc = request_device_code(
        proxy=proxy,
        relay_session=s if relay_url else None,
        retries=retries,
        retry_delay=retry_delay,
        log=log,
    )
    if not dc:
        return None
    log(f"  📋 user_code: {dc.get('user_code')}")

    try:
        retry_request(
            "device verification page",
            lambda: s.get(dc["verification_uri_complete"], impersonate="chrome", timeout=15),
            retries,
            retry_delay,
            log,
        )
        r = retry_request(
            "device verify",
            lambda: s.post(
                f"{OIDC_ISSUER}/oauth2/device/verify",
                data={"user_code": dc["user_code"]},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=15,
                allow_redirects=True,
            ),
            retries,
            retry_delay,
            log,
        )
        final_url = _response_url(r)
        if "consent" not in final_url:
            log(f"  ❌ verify 失败: {final_url}")
            return None
    except Exception as e:
        log(f"  ❌ verify 异常: {e}")
        return None

    try:
        r = retry_request(
            "device approve",
            lambda: s.post(
                f"{OIDC_ISSUER}/oauth2/device/approve",
                data={
                    "user_code": dc["user_code"],
                    "action": "allow",
                    "principal_type": "User",
                    "principal_id": "",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                impersonate="chrome",
                timeout=15,
                allow_redirects=True,
            ),
            retries,
            retry_delay,
            log,
        )
        final_url = _response_url(r)
        if "done" not in final_url:
            log(f"  ❌ approve 失败: {final_url}")
            return None
        log("  ✅ 授权确认")
    except Exception as e:
        log(f"  ❌ approve 异常: {e}")
        return None

    token = poll_token(
        dc["device_code"],
        dc.get("interval", 5),
        dc.get("expires_in", 1800),
        proxy=proxy,
        relay_session=s if relay_url else None,
        retries=retries,
        retry_delay=retry_delay,
        log=log,
    )
    if not token:
        return None
    log(
        f"  ✅ access_token (expires_in={token.get('expires_in')}s)"
        + (" + refresh_token" if token.get("refresh_token") else "")
    )
    return token


def token_to_auth_entry(token: dict, email: str = "") -> tuple[str, dict]:
    """
    返回 (top_level_key, entry)
    top_level_key 固定为 issuer::client_id（与 ~/.grok/auth.json 一致）
    """
    access = token.get("access_token") or token.get("key") or ""
    refresh = token.get("refresh_token") or ""
    payload = decode_jwt_payload(access)

    user_id = payload.get("sub") or payload.get("principal_id") or ""
    principal_id = payload.get("principal_id") or user_id
    principal_type = payload.get("principal_type") or "User"

    expires_in = int(token.get("expires_in") or 21600)
    # 优先用 JWT exp
    if "exp" in payload:
        expires_at = rfc3339_ns(float(payload["exp"]))
    else:
        expires_at = rfc3339_ns(time.time() + expires_in)

    iat = payload.get("iat")
    create_time = rfc3339_ns(float(iat) if iat else time.time())

    entry = {
        "key": access,
        "auth_mode": "oidc",
        "create_time": create_time,
        "user_id": user_id,
        "email": email or "",
        "principal_type": principal_type,
        "principal_id": principal_id,
        "refresh_token": refresh,
        "expires_at": expires_at,
        "oidc_issuer": OIDC_ISSUER,
        "oidc_client_id": CLIENT_ID,
    }
    return AUTH_KEY, entry


def _iso_utc_from_unix(ts) -> str:
    """unix 秒 → CPA 认的 RFC3339（秒级，带 Z）。"""
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return ""


def _safe_email_for_filename(email: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-@" else "_" for ch in email)
    return safe or "unknown"


def token_to_cpa_record(token: dict, email: str = "") -> dict:
    """token dict → CLIProxyAPI 扁平 xai auth 记录。

    对齐 CPA internal/auth/xai/token.go 的 TokenStorage 字段，以及
    grok-build-auth build_cliproxyapi_auth_record 的输出。
    """
    access = token.get("access_token") or token.get("key") or ""
    refresh = token.get("refresh_token") or ""
    id_token = token.get("id_token") or ""
    payload = decode_jwt_payload(access)
    id_payload = decode_jwt_payload(id_token) if id_token else {}

    if not email:
        email = id_payload.get("email") or payload.get("email") or ""
    sub = payload.get("sub") or id_payload.get("sub") or ""

    # expired: 优先 access token 的 exp，其次 expires_in 推算
    expired = ""
    if "exp" in payload:
        expired = _iso_utc_from_unix(payload["exp"])
    elif token.get("expires_in") is not None:
        try:
            expired = _iso_utc_from_unix(int(time.time()) + int(token["expires_in"]))
        except Exception:
            expired = ""

    return {
        "type": "xai",
        "auth_kind": "oauth",
        "email": email or "",
        "sub": sub,
        "access_token": access,
        "refresh_token": refresh,
        "id_token": id_token,
        "token_type": token.get("token_type", "Bearer"),
        "expires_in": token.get("expires_in", None),
        "expired": expired,
        "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "redirect_uri": "",
        "token_endpoint": CPA_TOKEN_ENDPOINT,
        "base_url": CPA_GROK_BASE_URL,
        "disabled": False,
        "headers": dict(CPA_GROK_HEADERS),
    }


def cpa_auth_filename(record: dict) -> str:
    """生成 CPA auth 文件名：xai-<email>.json。"""
    ident = str(record.get("email") or "").strip() or str(record.get("sub") or "").strip()
    safe = _safe_email_for_filename(ident)
    # 避免 email 本地部分已是 xai 时出现 "xai-xai..."
    fname = safe if safe.lower().startswith("xai") else f"xai-{safe}"
    return f"{fname}.json"


def write_cpa_auth(auth_dir: Path, record: dict) -> Path:
    """写出 CPA 可热加载的 xai-<email>.json（原子替换）。

    无 email 时用 sub(user_id) 命名，避免多个无 email 账号写成同一个
    xai-unknown.json 互相覆盖。
    """
    auth_dir.mkdir(parents=True, exist_ok=True)
    path = auth_dir / cpa_auth_filename(record)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def upload_cpa_auth_remote(
    base_url: str,
    management_key: str,
    record: dict,
    timeout: int = 30,
) -> str:
    """通过 CPA Management API 上传 auth 文件到远程实例。

    POST /v0/management/auth-files?name=<file.json>
    Header: Authorization: Bearer <management_key>
    Body: raw JSON auth record
    """
    import requests

    base = str(base_url or "").strip().rstrip("/")
    key = str(management_key or "").strip()
    if not base:
        raise ValueError("cpa_remote_url 为空")
    if not key:
        raise ValueError("cpa_management_key 为空")

    name = cpa_auth_filename(record)
    url = f"{base}/v0/management/auth-files"
    resp = requests.post(
        url,
        params={"name": name},
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        data=json.dumps(record, ensure_ascii=False).encode("utf-8"),
        timeout=timeout,
    )
    if resp.status_code >= 400:
        body = (resp.text or "").strip()
        if len(body) > 300:
            body = body[:300] + "..."
        raise RuntimeError(f"远程上传失败 HTTP {resp.status_code}: {body or resp.reason}")
    return name


def write_auth_json(path: Path, auth_key: str, entry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {auth_key: entry}
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def merge_auth_json(path: Path, auth_key: str, entry: dict, unique: bool = True) -> None:
    """
    合并写入。unique=True 时 key 变成 issuer::client_id::user_id，避免多账号互相覆盖。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    key = auth_key
    if unique and entry.get("user_id"):
        key = f"{auth_key}::{entry['user_id']}"
    existing[key] = entry
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def load_sso_list(path: str | None, single: str | None) -> list[str]:
    if single:
        return [single.strip()]
    if not path:
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 兼容 邮箱----密码----sso
        if "----" in line:
            parts = line.split("----")
            line = parts[-1].strip()
        out.append(line)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="SSO cookie → grok auth.json (纯 HTTP)")
    ap.add_argument("--sso", metavar="FILE", help="sso 列表文件（一行一个 JWT，或 邮箱----密码----sso）")
    ap.add_argument("--sso-cookie", metavar="JWT", help="单个 sso cookie")
    ap.add_argument("--out", default=None, help="输出 auth.json 路径（单账号或 --merge）")
    ap.add_argument(
        "--out-dir",
        default=None,
        help="批量时每个账号写一个 {user_id}.json（可直接 cp 到 ~/.grok/auth.json）",
    )
    ap.add_argument(
        "--merge",
        action="store_true",
        help="合并到 --out，key 用 issuer::client_id::user_id",
    )
    ap.add_argument("--delay", type=int, default=0, help="每个间隔秒数")
    ap.add_argument("--email", default="", help="写入 entry.email（可选）")
    ap.add_argument(
        "--cpa-auth-dir",
        default=None,
        help="额外写出 CLIProxyAPI 扁平格式 xai-<email>.json 到该目录（CPA 热加载）",
    )
    ap.add_argument(
        "--cpa-remote-url",
        default=None,
        help="远程 CPA 地址，如 http://你的CPA地址:8317；配合 --cpa-management-key 通过 Management API 上传",
    )
    ap.add_argument(
        "--cpa-management-key",
        default=None,
        help="远程 CPA 管理密钥（remote-management.secret-key 明文）",
    )
    ap.add_argument("--proxy", default="", help="device-flow 走代理，如 http://127.0.0.1:7890")
    ap.add_argument("--retries", type=int, default=3, help="每个临时网络失败后的额外重试次数")
    ap.add_argument("--retry-delay", type=float, default=1.0, help="首次重试等待秒数（后续指数退避）")
    ap.add_argument(
        "--relay-url",
        default="",
        help="header relay 地址；所有 accounts.x.ai/auth.x.ai 请求经该 relay 转发",
    )
    ap.add_argument("--relay-key", default="", help="可选 relay 的 x-relay-key")
    args = ap.parse_args()

    cookies = load_sso_list(args.sso, args.sso_cookie)
    if not cookies:
        ap.error("需要 --sso 或 --sso-cookie")

    if args.cpa_remote_url and not args.cpa_management_key:
        ap.error("使用 --cpa-remote-url 时必须同时提供 --cpa-management-key")
    if args.cpa_management_key and not args.cpa_remote_url:
        ap.error("使用 --cpa-management-key 时必须同时提供 --cpa-remote-url")
    if args.retries < 0:
        ap.error("--retries 不能小于 0")
    if args.retry_delay < 0:
        ap.error("--retry-delay 不能小于 0")

    if (
        len(cookies) > 1
        and not args.out_dir
        and not args.merge
        and not args.cpa_auth_dir
        and not args.cpa_remote_url
    ):
        # 默认批量写目录
        args.out_dir = args.out_dir or "./auth_out"
        print(f"批量模式默认 --out-dir {args.out_dir}")

    # 只指定 CPA 目标时不再默认写官方 ~/.grok/auth.json
    if (
        args.out is None
        and args.out_dir is None
        and not args.cpa_auth_dir
        and not args.cpa_remote_url
        and len(cookies) == 1
    ):
        args.out = str(Path.home() / ".grok" / "auth.json")

    print(f"🚀 SSO → auth.json: {len(cookies)} 个, delay={args.delay}s")
    ok = 0
    fail = 0

    for i, sso in enumerate(cookies, 1):
        print(f"\n{'=' * 60}\n[{i}/{len(cookies)}] ...\n{'=' * 60}")
        try:
            token = sso_to_token(
                sso,
                proxy=args.proxy,
                relay_url=args.relay_url,
                relay_key=args.relay_key,
                retries=args.retries,
                retry_delay=args.retry_delay,
            )
            if not token:
                fail += 1
                print(f"  ❌ [{i}] 失败")
                continue
            key, entry = token_to_auth_entry(token, email=args.email)
            uid = entry.get("user_id") or secrets.token_hex(4)

            if args.out_dir:
                p = Path(args.out_dir) / f"{uid}.json"
                write_auth_json(p, key, entry)
                print(f"  💾 {p}")
            if args.out:
                if args.merge or len(cookies) > 1:
                    merge_auth_json(Path(args.out), key, entry, unique=True)
                    print(f"  💾 merge → {args.out}")
                else:
                    write_auth_json(Path(args.out), key, entry)
                    print(f"  💾 {args.out}")

            if args.cpa_auth_dir or args.cpa_remote_url:
                record = token_to_cpa_record(token, email=args.email)
                if args.cpa_auth_dir:
                    cp = write_cpa_auth(Path(args.cpa_auth_dir), record)
                    print(f"  💾 CPA 本地 → {cp}")
                if args.cpa_remote_url:
                    name = upload_cpa_auth_remote(
                        args.cpa_remote_url,
                        args.cpa_management_key,
                        record,
                    )
                    print(f"  💾 CPA 远程 → {args.cpa_remote_url.rstrip('/')}/.../{name}")

            ok += 1
            print(f"  ✅ [{i}] 完成 user_id={uid[:12]}...")
        except Exception as e:
            fail += 1
            print(f"  ❌ [{i}] 异常: {e}")

        if args.delay > 0 and i < len(cookies):
            time.sleep(args.delay)

    print(f"\n{'=' * 60}\n📊 完成: {ok}/{len(cookies)} 成功, {fail} 失败")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
