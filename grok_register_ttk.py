#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Grok 注册机 - TTK GUI 版本
整合 DrissionPage_example.py, openai_register.py, batch_open_nsfw.py
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import threading
import datetime
import time
import os
import sys
import signal
import gc
import queue
import secrets
import struct
import random
import re
import string
import json

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

from DrissionPage import Chromium, ChromiumOptions
from DrissionPage.errors import PageDisconnectedError
from curl_cffi import requests

# SSO → CLIProxyAPI(CPA) 扁平格式转换（复用 sso_to_auth_json 的授权码流程 + 写入器）
import sso_to_auth_json as _s2cpa


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
MEMORY_CLEANUP_INTERVAL = 5

UI_BG = "#242424"
UI_PANEL_BG = "#2b2b2b"
UI_FG = "#f2f2f2"
UI_MUTED_FG = "#b8b8b8"
UI_ENTRY_BG = "#333333"
UI_BUTTON_BG = "#3a3a3a"
UI_ACTIVE_BG = "#4a6078"

DEFAULT_CONFIG = {
    "duckmail_api_key": "",
    "cloudflare_api_base": "",
    "cloudflare_api_key": "",
    "cloudflare_auth_mode": "none",
    "cloudflare_custom_auth": "",
    "cloudflare_path_domains": "/api/domains",
    "cloudflare_path_accounts": "/api/new_address",
    "cloudflare_path_token": "/api/token",
    "cloudflare_path_messages": "/api/mails",
    "proxy": "http://127.0.0.1:7890",
    "enable_nsfw": True,
    "register_count": 1,
    "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    # CLIProxyAPI(CPA) 直出：注册拿到 SSO 后自动走授权码流程换 token 并写成 CPA 扁平格式
    "cpa_auto_add": False,
    "cpa_auth_dir": "",
    # 远程 CPA：通过 Management API POST /v0/management/auth-files 上传
    "cpa_remote_url": "",
    "cpa_management_key": "",
    # Browser driver: local Chromium | Browser Use Cloud | RoxyBrowser fingerprint
    "browser_driver": "local",  # local | browser_use | roxy
    "browser_use_api_key": "",
    "browser_use_proxy_country": "us",
    "browser_use_use_proxy": True,
    "browser_use_profile_id": "",
    "browser_use_cdp_base": "wss://connect.browser-use.com",
    "browser_use_timeout_minutes": 15,
    "browser_use_nav_timeout": 90,
    "browser_use_keep_open": False,
    "browser_use_screen_width": 0,
    "browser_use_screen_height": 0,
    "browser_use_extra_query": {},
    # RoxyBrowser local fingerprint browser (API default http://127.0.0.1:50000)
    "roxy_api_base": "http://127.0.0.1:50000",
    "roxy_api_token": "",
    "roxy_workspace_id": "",
    "roxy_project_id": "",
    "roxy_profile_id": "",  # leave empty when one-profile-per-account
    "roxy_one_profile_per_account": True,
    "roxy_delete_profile_after_run": True,
    "roxy_keep_browser_open": False,
    "roxy_open_headless": False,
    "roxy_default_os": "Windows",
    "roxy_default_os_version": "",
    "roxy_create_use_proxy": False,
    "roxy_proxy_check_channel": "",
    "roxy_profile_name": "grok-register",
    "roxy_api_timeout": 90,
    # CapMonster Cloud — Turnstile token solver (https://docs.capmonster.cloud/)
    "capmonster_api_key": "",
    "capmonster_api_base": "https://api.capmonster.cloud",
    "capmonster_enabled": True,  # used when api_key is set
    "capmonster_timeout": 120,
    "capmonster_poll_interval": 2.0,
    # Optional hardcode if page extraction fails (0x4AAAAA… from CF dashboard / iframe URL)
    "capmonster_website_key": "",
    # If CapMonster fails, fall back to local checkbox click (usually fails on managed CF)
    "capmonster_fallback_click": True,
}

config = DEFAULT_CONFIG.copy()
_cf_domain_index = 0


class RegistrationCancelled(Exception):
    pass


class AccountRetryNeeded(Exception):
    pass


def load_config():
    global config
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            config = {**DEFAULT_CONFIG, **loaded}
        except Exception:
            config = DEFAULT_CONFIG.copy()
    return config


def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except Exception as e:
        print(f"保存配置失败: {e}")


def ensure_stable_python_runtime():
    if sys.version_info < (3, 14) or os.environ.get("DPE_REEXEC_DONE") == "1":
        return

    local_app_data = os.environ.get("LOCALAPPDATA", "")
    candidates = [
        os.path.join(local_app_data, "Programs", "Python", "Python312", "python.exe"),
        os.path.join(local_app_data, "Programs", "Python", "Python313", "python.exe"),
    ]

    current_python = os.path.normcase(os.path.abspath(sys.executable))
    for candidate in candidates:
        if not os.path.isfile(candidate):
            continue
        if os.path.normcase(os.path.abspath(candidate)) == current_python:
            return

        print(
            f"[*] 检测到 Python {sys.version.split()[0]}，自动切换到更稳定的解释器: {candidate}"
        )
        env = os.environ.copy()
        env["DPE_REEXEC_DONE"] = "1"
        os.execve(candidate, [candidate, os.path.abspath(__file__), *sys.argv[1:]], env)


def warn_runtime_compatibility():
    if sys.version_info >= (3, 14):
        print(
            "[提示] 当前 Python 为 3.14+；若出现 Mail.tm TLS 异常，建议改用 Python 3.12 或 3.13。"
        )


ensure_stable_python_runtime()
warn_runtime_compatibility()

load_config()

EXTENSION_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "turnstilePatch")
)


DUCKMAIL_API_BASE = "https://api.duckmail.sbs"


def get_proxies():
    proxy = config.get("proxy", "")
    if proxy:
        return {"http": proxy, "https": proxy}
    return {}


def get_browser_driver():
    """Return normalized browser driver: 'local' | 'browser_use' | 'roxy'."""
    raw = str(config.get("browser_driver", "local") or "local").strip().lower()
    if raw in ("browser_use", "browseruse", "browser-use", "bu", "cloud"):
        return "browser_use"
    if raw in ("roxy", "roxybrowser", "roxy_browser", "fingerprint"):
        return "roxy"
    return "local"


def is_browser_use_driver():
    return get_browser_driver() == "browser_use"


def is_roxy_driver():
    return get_browser_driver() == "roxy"


def get_duckmail_api_key():
    return config.get("duckmail_api_key", "")


def get_cloudflare_api_base():
    return str(config.get("cloudflare_api_base", "") or "").rstrip("/")


def get_cloudflare_api_key():
    return config.get("cloudflare_api_key", "")


def get_cloudflare_auth_mode():
    return str(config.get("cloudflare_auth_mode", "none") or "none").lower()


def get_cloudflare_custom_auth():
    """全局访问密码（cloudflare_temp_email 的 PASSWORDS）。

    开启后 Worker 会对除 /open_api、/telegram 外的所有路径校验 x-custom-auth 头，
    与 cloudflare_auth_mode 正交叠加，需要在每个请求上单独注入。
    """
    return str(config.get("cloudflare_custom_auth", "") or "").strip()


def cloudflare_apply_custom_auth(headers):
    """给请求头注入全局访问密码，若未配置则原样返回。"""
    custom_auth = get_cloudflare_custom_auth()
    if custom_auth:
        headers["x-custom-auth"] = custom_auth
    return headers


def get_cloudflare_path(key, default_path):
    raw = str(config.get(key, default_path) or default_path).strip()
    if not raw.startswith("/"):
        raw = "/" + raw
    return raw


def cloudflare_build_headers(content_type=False):
    headers = {"Content-Type": "application/json"} if content_type else {}
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key:
        if mode == "x-api-key":
            headers["X-API-Key"] = key
        elif mode == "x-admin-auth":
            headers["x-admin-auth"] = key
        elif mode != "none":
            headers["Authorization"] = f"Bearer {key}"
    cloudflare_apply_custom_auth(headers)
    return headers


def cloudflare_apply_auth_params(params=None):
    merged = dict(params or {})
    key = get_cloudflare_api_key()
    mode = get_cloudflare_auth_mode()
    if key and mode == "query-key":
        merged["key"] = key
    return merged


def cloudflare_next_default_domain():
    """按配置轮换选择 Cloudflare 临时邮箱域名。"""
    global _cf_domain_index
    domains = [x.strip() for x in str(config.get("defaultDomains", "") or "").split(",") if x.strip()]
    if not domains:
        return ""
    domain = domains[_cf_domain_index % len(domains)]
    _cf_domain_index += 1
    return domain


def cloudflare_is_admin_create_path(path):
    """判断当前创建邮箱路径是否为 cloudflare_temp_email 管理员创建接口。"""
    return str(path or "").rstrip("/").lower() == "/admin/new_address"


def _pick_list_payload(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        if isinstance(data.get("results"), list):
            return data.get("results")
        if isinstance(data.get("hydra:member"), list):
            return data.get("hydra:member")
        if isinstance(data.get("data"), list):
            return data.get("data")
        if isinstance(data.get("messages"), list):
            return data.get("messages")
        if isinstance(data.get("data"), dict):
            nested = data.get("data")
            if isinstance(nested.get("messages"), list):
                return nested.get("messages")
    return []


def cloudflare_create_temp_address(api_base):
    """适配 cloudflare_temp_email 新建地址接口并兼容 admin 创建模式。"""
    path = get_cloudflare_path("cloudflare_path_accounts", "/api/new_address")
    url = f"{api_base}{path}"
    domain = cloudflare_next_default_domain()
    is_admin_create = cloudflare_is_admin_create_path(path)
    if is_admin_create:
        payload = {"name": generate_username(10), "enablePrefix": True}
        if domain:
            payload["domain"] = domain
        headers = cloudflare_build_headers(content_type=True)
    else:
        payload = {}
        if domain:
            payload["domain"] = domain
        headers = cloudflare_apply_custom_auth({"Content-Type": "application/json"})
    resp = http_post(url, json=payload, headers=headers)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare {path} 返回非JSON: {resp.text[:300]}")
    address = data.get("address")
    jwt = data.get("jwt")
    if not address or not jwt:
        raise Exception(f"Cloudflare {path} 缺少 address/jwt: {data}")
    return address, jwt


def get_user_agent():
    return config.get(
        "user_agent",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    )


def _normalize_sso_token(raw_token):
    token = str(raw_token or "").strip()
    if token.startswith("sso="):
        token = token[4:]
    return token


def _resolve_cpa_proxy():
    """CPA 换 token 用的代理：config.proxy，否则环境变量。空字符串 = 直连。

    Do NOT force 127.0.0.1:7890 — a dead local proxy made valid SSOs look failed.
    """
    proxy = str(config.get("proxy", "") or "").strip()
    if proxy:
        return proxy
    for key in ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY"):
        val = str(os.environ.get(key, "") or "").strip()
        if val:
            return val
    return ""


def build_cpa_export_note():
    """Build CPA json ``note``: ``Roxy - Capmonster - No Proxy`` style.

    - driver always present (Roxy / Browser Use / Local)
    - Capmonster only when CapMonster Cloud was enabled for this run
    - proxy string when set, else ``No Proxy``
    """
    try:
        driver = get_browser_driver()
    except Exception:
        driver = str(config.get("browser_driver") or "local").strip().lower()
    driver_label = {
        "roxy": "Roxy",
        "browser_use": "Browser Use",
        "local": "Local",
    }.get(driver, (driver or "Local").replace("_", " ").title())

    parts = [driver_label]
    try:
        if capmonster_is_enabled():
            parts.append("Capmonster")
    except Exception:
        if str(config.get("capmonster_api_key") or "").strip() and config.get(
            "capmonster_enabled", True
        ):
            parts.append("Capmonster")

    proxy = str(config.get("proxy") or "").strip()
    if not proxy and driver == "browser_use" and config.get("browser_use_use_proxy", True):
        country = str(config.get("browser_use_proxy_country") or "").strip()
        proxy = f"BrowserUse:{country}" if country else "BrowserUse proxy"
    if not proxy and driver == "roxy" and config.get("roxy_create_use_proxy"):
        # Roxy may inject profile proxy from config.proxy already covered above
        proxy = str(config.get("proxy") or "").strip()
    parts.append(proxy if proxy else "No Proxy")
    return " - ".join(parts)


def add_sso_to_cpa(raw_token, email="", log_callback=None):
    """SSO → 授权码流程换 token → 写入本地 CPA auth 目录和/或远程 CPA。

    SSO 本身不是 CPA 认的凭据；必须先用授权码流程（referrer=grok-build）
    换到 access/refresh token，再写成 CPA 的 xai-<email>.json
    （type=xai + cli-chat-proxy base_url + grok-cli headers）。

    - 本地：写入 cpa_auth_dir，CPA 监听热加载
    - 远程：POST Management API /v0/management/auth-files（cpa_remote_url + cpa_management_key）
    """
    if not config.get("cpa_auto_add", False):
        return
    auth_dir = str(config.get("cpa_auth_dir", "") or "").strip()
    remote_url = str(config.get("cpa_remote_url", "") or "").strip()
    management_key = str(config.get("cpa_management_key", "") or "").strip()
    if not auth_dir and not remote_url:
        if log_callback:
            log_callback("[Debug] 已开启 CPA 直出但未配置 cpa_auth_dir 或 cpa_remote_url，跳过")
        return
    if remote_url and not management_key:
        if log_callback:
            log_callback("[Debug] 已配置 cpa_remote_url 但未配置 cpa_management_key，跳过远程上传")
        remote_url = ""
    if not auth_dir and not remote_url:
        return
    sso = _normalize_sso_token(raw_token)
    if not sso:
        return
    proxy = _resolve_cpa_proxy()
    note = build_cpa_export_note()

    def _cpa_log(message):
        if log_callback:
            log_callback(f"[CPA] {str(message).strip()}")

    try:
        _cpa_log(
            f"SSO → 授权码流程换 token (proxy={proxy or 'direct'}) note={note!r} ..."
        )
        token = _s2cpa.sso_to_token(sso, proxy=proxy or None, log=_cpa_log)
        if not token and proxy:
            _cpa_log("代理换 token 失败，重试直连…")
            token = _s2cpa.sso_to_token(sso, proxy=None, log=_cpa_log)
        if not token:
            _cpa_log("授权码流程换 token 失败，跳过")
            return
        record = _s2cpa.token_to_cpa_record(token, email=email, sso=sso, note=note)
        ap = _s2cpa.decode_jwt_payload(record.get("access_token", ""))
        ref = ap.get("referrer")
        if ref != "grok-build":
            _cpa_log(f"警告: access_token referrer={ref!r}，预期 grok-build")
        else:
            _cpa_log("access_token referrer=grok-build OK")
        _cpa_log(f"note={record.get('note')!r}")
        if auth_dir:
            try:
                path = _s2cpa.write_cpa_auth(_s2cpa.Path(auth_dir), record)
                _cpa_log(f"已写入本地 {path}")
            except Exception as local_exc:
                _cpa_log(f"本地写入失败: {local_exc}")
        if remote_url:
            try:
                name = _s2cpa.upload_cpa_auth_remote(remote_url, management_key, record)
                _cpa_log(f"已上传远程 {remote_url.rstrip('/')}/.../{name}")
            except Exception as remote_exc:
                _cpa_log(f"远程上传失败: {remote_exc}")
    except Exception as exc:
        _cpa_log(f"直出失败: {exc}")

def create_browser_options():
    options = ChromiumOptions()
    options.auto_port()
    # eager = DOMContentLoaded is enough for SPA signup UIs.
    # normal waits for window load + idle network; accounts.x.ai often keeps the
    # tab spinner alive (analytics/ws), so page.get/doc_loaded would hang forever.
    options.set_load_mode("eager")
    # base was 1s before, which made wait.doc_loaded give up almost immediately
    # and made element waits flaky. Keep page_load bounded so get() cannot stall.
    options.set_timeouts(base=15, page_load=20, script=30)
    if os.path.exists(EXTENSION_PATH):
        options.add_extension(EXTENSION_PATH)
    return options


def _build_request_kwargs(**kwargs):
    request_kwargs = dict(kwargs)
    proxies = request_kwargs.pop("proxies", None)
    if proxies is None:
        proxies = get_proxies()
    if proxies:
        request_kwargs["proxies"] = proxies
    request_kwargs.setdefault("timeout", 15)
    return request_kwargs


def http_get(url, **kwargs):
    try:
        return requests.get(url, **_build_request_kwargs(**kwargs))
    except Exception as exc:
        err = str(exc)
        # 代理不可用时自动回退为直连，避免整个流程直接失败
        if "127.0.0.1 port 7890" in err or "Could not connect to server" in err:
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.get(url, **_build_request_kwargs(**retry_kwargs))
        raise


def http_post(url, **kwargs):
    try:
        return requests.post(url, **_build_request_kwargs(**kwargs))
    except Exception as exc:
        err = str(exc)
        if "127.0.0.1 port 7890" in err or "Could not connect to server" in err:
            retry_kwargs = dict(kwargs)
            retry_kwargs["proxies"] = {}
            return requests.post(url, **_build_request_kwargs(**retry_kwargs))
        raise


def raise_if_cancelled(cancel_callback=None):
    if cancel_callback and cancel_callback():
        raise RegistrationCancelled("用户停止注册")


def sleep_with_cancel(seconds, cancel_callback=None):
    deadline = time.time() + max(seconds, 0)
    while True:
        raise_if_cancelled(cancel_callback)
        remaining = deadline - time.time()
        if remaining <= 0:
            return
        time.sleep(min(0.2, remaining))


def get_domains(api_key=None):
    headers = {}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = http_get(f"{DUCKMAIL_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def create_account(address, password, api_key=None, expires_in=0):
    headers = {"Content-Type": "application/json"}
    key = api_key or get_duckmail_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = {"address": address, "password": password, "expiresIn": expires_in}
    resp = http_post(f"{DUCKMAIL_API_BASE}/accounts", json=data, headers=headers)
    resp.raise_for_status()
    return resp.json()


def get_token(address, password):
    data = {"address": address, "password": password}
    resp = http_post(f"{DUCKMAIL_API_BASE}/token", json=data)
    resp.raise_for_status()
    return resp.json().get("token")


def get_messages(token):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages", headers=headers)
    resp.raise_for_status()
    return resp.json().get("hydra:member", [])


def get_message_detail(token, message_id):
    headers = {"Authorization": f"Bearer {token}"}
    resp = http_get(f"{DUCKMAIL_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_domains(api_base, api_key=None):
    headers = cloudflare_build_headers(content_type=False)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_domains", "/domains")
    params = cloudflare_apply_auth_params()
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    return _pick_list_payload(resp.json())


def cloudflare_create_account(api_base, address, password, api_key=None, expires_in=0):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    payload = {"address": address, "password": password, "expiresIn": expires_in}
    path = get_cloudflare_path("cloudflare_path_accounts", "/accounts")
    params = cloudflare_apply_auth_params()
    resp = http_post(f"{api_base}{path}", json=payload, headers=headers, params=params)
    resp.raise_for_status()
    return resp.json()


def cloudflare_get_token(api_base, address, password, api_key=None):
    headers = cloudflare_build_headers(content_type=True)
    if api_key and "Authorization" in headers:
        headers["Authorization"] = f"Bearer {api_key}"
    if api_key and "X-API-Key" in headers:
        headers["X-API-Key"] = api_key
    path = get_cloudflare_path("cloudflare_path_token", "/token")
    resp = http_post(
        f"{api_base}{path}",
        json={"address": address, "password": password},
        headers=headers,
        params=cloudflare_apply_auth_params(),
    )
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, dict):
        if data.get("token"):
            return data.get("token")
        if isinstance(data.get("data"), dict) and data["data"].get("token"):
            return data["data"].get("token")
    return None


def cloudflare_get_messages(api_base, token):
    headers = cloudflare_apply_custom_auth({"Authorization": f"Bearer {token}"})
    path = get_cloudflare_path("cloudflare_path_messages", "/messages")
    params = {"limit": 20, "offset": 0}
    params = cloudflare_apply_auth_params(params)
    resp = http_get(f"{api_base}{path}", headers=headers, params=params)
    resp.raise_for_status()
    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Cloudflare messages 返回非JSON: {resp.text[:300]}")
    return _pick_list_payload(data)


def cloudflare_get_message_detail(api_base, token, message_id):
    headers = cloudflare_apply_custom_auth({"Authorization": f"Bearer {token}"})
    candidates = [
        f"{api_base}/api/mail/{message_id}",
        f"{api_base}{get_cloudflare_path('cloudflare_path_messages', '/messages')}/{message_id}",
    ]
    last_err = None
    for url in candidates:
        try:
            resp = http_get(
                url,
                headers=headers,
                params=cloudflare_apply_auth_params(),
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, dict) and isinstance(data.get("data"), dict):
                return data["data"]
            return data
        except Exception as exc:
            last_err = exc
            continue
    raise Exception(f"Cloudflare 获取邮件详情失败: {last_err}")


YYDS_API_BASE = "https://maliapi.215.im/v1"


def get_yyds_api_key():
    return config.get("yyds_api_key", "")


def get_yyds_jwt():
    return config.get("yyds_jwt", "")


def yyds_get_domains(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/domains", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    return data.get("data", []) if data.get("success") else []


def yyds_create_account(address=None, domain=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    payload = {}
    if address:
        payload["address"] = address
    if domain:
        payload["domain"] = domain
    elif key or token:
        payload["autoDomainStrategy"] = "prefer_owned"
    resp = http_post(f"{YYDS_API_BASE}/accounts", json=payload, headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 创建邮箱失败: {data}")


def yyds_get_token(address, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_post(
        f"{YYDS_API_BASE}/token", json={"address": address}, headers=headers
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("token")
    raise Exception(f"YYDS 获取token失败: {data}")


def yyds_get_messages(address, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(
        f"{YYDS_API_BASE}/messages",
        params={"address": address},
        headers=headers,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {}).get("messages", [])
    return []


def yyds_get_message_detail(message_id, token=None, api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    temp_token = token or jwt or get_yyds_jwt()
    headers = {}
    if temp_token:
        headers["Authorization"] = f"Bearer {temp_token}"
    elif key:
        headers["X-API-Key"] = key
    resp = http_get(f"{YYDS_API_BASE}/messages/{message_id}", headers=headers)
    resp.raise_for_status()
    data = resp.json()
    if data.get("success"):
        return data.get("data", {})
    raise Exception(f"YYDS 获取邮件详情失败: {data}")


def yyds_generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def yyds_pick_domain(api_key=None, jwt=None):
    domains = yyds_get_domains(api_key=api_key, jwt=jwt)
    if not domains:
        raise Exception("YYDS 没有返回任何可用域名")
    private = [d for d in domains if d.get("isVerified") and not d.get("isPublic")]
    if private:
        return private[0]["domain"]
    public = [d for d in domains if d.get("isVerified") and d.get("isPublic")]
    if public:
        return public[0]["domain"]
    verified = [d for d in domains if d.get("isVerified")]
    if verified:
        return verified[0]["domain"]
    raise Exception("YYDS 无已验证域名可用")


def yyds_get_email_and_token(api_key=None, jwt=None):
    key = api_key or get_yyds_api_key()
    token = jwt or get_yyds_jwt()
    if not token and not key:
        raise Exception("YYDS API Key 或 JWT 未配置")
    domain = yyds_pick_domain(api_key=key, jwt=token)
    username = yyds_generate_username(10)
    result = yyds_create_account(
        address=username, domain=domain, api_key=key, jwt=token
    )
    address = result.get("address") or f"{username}@{domain}"
    temp_token = result.get("token")
    if not temp_token:
        temp_token = yyds_get_token(address, api_key=key, jwt=token)
    if not temp_token:
        raise Exception("获取 YYDS token 失败")
    print(f"[*] 已创建 YYDS 邮箱: {address}")
    return address, temp_token


def yyds_get_oai_code(
    token,
    address,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    jwt=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = yyds_get_messages(address, token=token, jwt=jwt)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] YYDS 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            to_addrs = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if address.lower() not in to_addrs:
                continue
            try:
                detail = yyds_get_message_detail(msg_id, token=token, jwt=jwt)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] YYDS 获取邮件详情失败: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] YYDS 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] YYDS 从邮件中提取到验证码: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"YYDS 在 {timeout}s 内未收到验证码邮件")


def generate_username(length=10):
    chars = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(chars) for _ in range(length))


def pick_domain(api_key=None):
    domains = get_domains(api_key=api_key)
    if not domains:
        raise Exception("DuckMail 没有返回任何可用域名")
    private = [d for d in domains if d.get("ownerId")]
    verified_private = [d for d in private if d.get("isVerified")]
    if verified_private:
        return verified_private[0]["domain"]
    public = [d for d in domains if d.get("isVerified")]
    if public:
        return public[0]["domain"]
    raise Exception("DuckMail 无已验证域名可用")


def get_email_provider():
    return config.get("email_provider", "duckmail")


def get_email_and_token(api_key=None):
    provider = get_email_provider()
    if provider == "yyds":
        return yyds_get_email_and_token(api_key=api_key, jwt=get_yyds_jwt())
    if provider == "cloudflare":
        api_base = get_cloudflare_api_base()
        if not api_base:
            raise Exception("Cloudflare API Base 未配置")
        try:
            # cloudflare_temp_email 专用模式
            return cloudflare_create_temp_address(api_base)
        except Exception as primary_exc:
            # 兜底回退到 Mail.tm 风格
            key = api_key or get_cloudflare_api_key()
            domains = cloudflare_get_domains(api_base, api_key=key)
            if not domains:
                raise Exception(f"Cloudflare 创建邮箱失败: {primary_exc}")
            verified = [d for d in domains if d.get("isVerified")]
            target = verified[0] if verified else domains[0]
            domain = target.get("domain")
            if not domain:
                raise Exception("Cloudflare 域名数据格式错误，缺少 domain 字段")
            username = generate_username(10)
            address = f"{username}@{domain}"
            password = secrets.token_urlsafe(12)
            cloudflare_create_account(
                api_base, address, password, api_key=key, expires_in=0
            )
            token = cloudflare_get_token(api_base, address, password, api_key=key)
            if not token:
                raise Exception("获取 Cloudflare 邮箱 token 失败")
            return address, token
    key = api_key or get_duckmail_api_key()
    domain = pick_domain(api_key=key)
    username = generate_username(10)
    address = f"{username}@{domain}"
    password = secrets.token_urlsafe(12)
    create_account(address, password, api_key=key, expires_in=0)
    token = get_token(address, password)
    if not token:
        raise Exception("获取 DuckMail token 失败")
    return address, token


def get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    provider = get_email_provider()
    if provider == "yyds":
        return yyds_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            jwt=get_yyds_jwt(),
            cancel_callback=cancel_callback,
        )
    if provider == "cloudflare":
        return cloudflare_get_oai_code(
            dev_token,
            email,
            timeout=timeout,
            poll_interval=poll_interval,
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            resend_callback=resend_callback,
        )
    return duckmail_get_oai_code(
        dev_token,
        email,
        timeout=timeout,
        poll_interval=poll_interval,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
    )


def extract_verification_code(text, subject=""):
    if subject:
        match = re.search(r"^([A-Z0-9]{3}-[A-Z0-9]{3})\s+xAI", subject, re.IGNORECASE)
        if match:
            return match.group(1)
    match = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", text, re.IGNORECASE)
    if match:
        return match.group(1)
    patterns = [
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def duckmail_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
):
    deadline = time.time() + timeout
    seen_ids = set()
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            messages = get_messages(dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id or msg_id in seen_ids:
                continue
            seen_ids.add(msg_id)
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            if email.lower() not in recipients:
                continue
            try:
                detail = get_message_detail(dev_token, msg_id)
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 获取邮件详情失败: {exc}")
                continue
            parts = []
            text_body = detail.get("text") or ""
            if text_body:
                parts.append(text_body)
            html_list = detail.get("html") or []
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            combined = "\n".join(parts)
            subject = detail.get("subject", "")
            if log_callback:
                log_callback(f"[Debug] 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] 从邮件中提取到验证码: {code}")
                return code
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"在 {timeout}s 内未收到验证码邮件")


def cloudflare_get_oai_code(
    dev_token,
    email,
    timeout=180,
    poll_interval=3,
    log_callback=None,
    cancel_callback=None,
    resend_callback=None,
):
    api_base = get_cloudflare_api_base()
    if not api_base:
        raise Exception("Cloudflare API Base 未配置")
    deadline = time.time() + timeout
    # 同一封邮件正文可能延迟可读，允许多次重试解析，避免偶发漏码
    seen_attempts = {}
    next_resend_at = time.time() + 35
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if resend_callback and time.time() >= next_resend_at:
            try:
                resend_callback()
                if log_callback:
                    log_callback("[*] 已触发重新发送验证码")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 触发重发验证码失败: {exc}")
            next_resend_at = time.time() + 35
        try:
            messages = cloudflare_get_messages(api_base, dev_token)
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Cloudflare 拉取邮件列表失败: {exc}")
            sleep_with_cancel(poll_interval, cancel_callback)
            continue
        if log_callback:
            log_callback(f"[Debug] Cloudflare 本轮邮件数量: {len(messages)}")

        for msg in messages:
            msg_id = msg.get("id") or msg.get("msgid")
            if not msg_id:
                continue
            attempt = int(seen_attempts.get(msg_id, 0))
            if attempt >= 5:
                continue
            seen_attempts[msg_id] = attempt + 1
            recipients = [t.get("address", "").lower() for t in (msg.get("to") or [])]
            msg_addr = str(msg.get("address", "")).lower()
            # 优先匹配目标邮箱；若结构不一致也允许继续解析，避免接口字段漂移导致漏码
            address_matched = True
            if recipients:
                address_matched = email.lower() in recipients
            elif msg_addr:
                address_matched = msg_addr == email.lower()
            if not address_matched and log_callback:
                log_callback(f"[Debug] 跳过疑似非目标邮件 id={msg_id} address={msg_addr} to={recipients}")
                continue
            parts = []
            # 先直接从列表项取内容，避免 detail 接口差异导致漏码
            for field in ("text", "raw", "content", "intro", "body", "snippet"):
                value = msg.get(field)
                if isinstance(value, str) and value.strip():
                    parts.append(value)
            html_list = msg.get("html") or []
            if isinstance(html_list, str):
                html_list = [html_list]
            for h in html_list:
                parts.append(re.sub(r"<[^>]+>", " ", h))
            subject = str(msg.get("subject", "") or "")
            combined = "\n".join(parts)
            # 再尝试 detail 接口补全内容
            try:
                detail = cloudflare_get_message_detail(api_base, dev_token, msg_id)
                for field in ("text", "raw", "content", "intro", "body", "snippet"):
                    value = detail.get(field)
                    if isinstance(value, str) and value.strip():
                        combined += "\n" + value
                html_list2 = detail.get("html") or []
                if isinstance(html_list2, str):
                    html_list2 = [html_list2]
                for h in html_list2:
                    combined += "\n" + re.sub(r"<[^>]+>", " ", h)
                if not subject:
                    subject = str(detail.get("subject", "") or "")
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] Cloudflare detail接口失败，改用列表内容解析: {exc}")
            if log_callback:
                log_callback(f"[Debug] Cloudflare 收到邮件: {subject}")
            code = extract_verification_code(combined, subject)
            if code:
                if log_callback:
                    log_callback(f"[*] Cloudflare 从邮件中提取到验证码: {code}")
                return code
            elif log_callback:
                log_callback(f"[Debug] 邮件已解析但未提取到验证码 id={msg_id} attempt={seen_attempts[msg_id]}")
        sleep_with_cancel(poll_interval, cancel_callback)
    raise Exception(f"Cloudflare 在 {timeout}s 内未收到验证码邮件")


def generate_random_birthdate():
    import datetime as dt

    today = dt.date.today()
    age = random.randint(20, 40)
    birth_year = today.year - age
    birth_month = random.randint(1, 12)
    birth_day = random.randint(1, 28)
    return f"{birth_year}-{birth_month:02d}-{birth_day:02d}T16:00:00.000Z"


def response_preview(res, limit=200):
    """安全预览 HTTP 响应体；gRPC/二进制内容不直接当文本打印。"""
    try:
        headers = {str(k).lower(): str(v).lower() for k, v in dict(getattr(res, "headers", {}) or {}).items()}
        content_type = headers.get("content-type", "")
        raw = getattr(res, "content", None)
        if raw is None:
            try:
                raw = (res.text or "").encode("utf-8", errors="replace")
            except Exception:
                raw = b""
        if not isinstance(raw, (bytes, bytearray)):
            raw = str(raw).encode("utf-8", errors="replace")
        raw = bytes(raw)

        # gRPC / protobuf 常见 content-type 或正文以不可打印字节为主
        is_binaryish = (
            "grpc" in content_type
            or "protobuf" in content_type
            or "octet-stream" in content_type
            or (raw[:1] in (b"\x00", b"\x01") and b"grpc-status" in raw)
        )
        if is_binaryish or (raw and sum(1 for b in raw[:64] if b < 9 or (13 < b < 32)) > 8):
            # 尽量抽出可读的 trailer 片段（如 grpc-status:0）
            readable = re.findall(rb"[ -~]{3,}", raw)
            text = " ".join(part.decode("ascii", errors="ignore") for part in readable)
            text = re.sub(r"\s+", " ", text).strip()
            if not text:
                text = f"<binary {len(raw)} bytes>"
            return text[:limit]

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]
    except Exception:
        return ""


def is_cloudflare_block_response(res):
    try:
        headers = {str(k).lower(): str(v).lower() for k, v in dict(res.headers).items()}
        text = str(res.text or "").lower()
        server = headers.get("server", "")
        content_type = headers.get("content-type", "")
        return (
            res.status_code in (403, 429, 503)
            and (
                "cloudflare" in server
                or "cloudflare" in text
                or "cf-error" in text
                or "__cf_chl" in text
                or "text/html" in content_type
            )
        )
    except Exception:
        return False


def set_birth_date(session, log_callback=None):
    url = "https://grok.com/rest/auth/set-birth-date"
    new_headers = {
        "content-type": "application/json",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    payload = {"birthDate": generate_random_birthdate()}
    try:
        res = session.post(url, json=payload, headers=new_headers, timeout=15)
        body_preview = response_preview(res)
        if log_callback:
            log_callback(
                f"[Debug] set_birth_date status: {res.status_code}, body: {body_preview}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        # 生日一旦写过就不能改；算已完成，不能当失败中断后续 NSFW
        text = str(res.text or "")
        if res.status_code in (400, 409, 429) and (
            "birth-date-change-limit-reached" in text
            or "Birth date is locked" in text
            or "already set" in text.lower()
        ):
            return True, "already_set"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_birth_date 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_birth_date HTTP {res.status_code}: {body_preview}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_birth_date] 异常: {e}")
        return False, f"set_birth_date 异常: {e}"


def set_tos_accepted(session, log_callback=None):
    url = "https://accounts.x.ai/auth_mgmt.AuthManagement/SetTosAcceptedVersion"
    payload = struct.pack("B", (2 << 3) | 0) + struct.pack("B", 1)
    data = b"\x00" + struct.pack(">I", len(payload)) + payload
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "x-user-agent": "connect-es/2.1.1",
        "origin": "https://accounts.x.ai",
        "referer": "https://accounts.x.ai/accept-tos",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(f"[Debug] set_tos_accepted status: {res.status_code}")
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "set_tos_accepted 被 accounts.x.ai 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"set_tos_accepted HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[set_tos_accepted] 异常: {e}")
        return False, f"set_tos_accepted 异常: {e}"


def encode_grpc_nsfw_settings():
    field1_content = bytes([0x10, 0x01])
    field1 = bytes([0x0A, len(field1_content)]) + field1_content
    nsfw_string = b"always_show_nsfw_content"
    field2_inner = bytes([0x0A, len(nsfw_string)]) + nsfw_string
    field2 = bytes([0x12, len(field2_inner)]) + field2_inner
    payload = field1 + field2
    return b"\x00" + struct.pack(">I", len(payload)) + payload


def update_nsfw_settings(session, log_callback=None):
    url = "https://grok.com/auth_mgmt.AuthManagement/UpdateUserFeatureControls"
    data = encode_grpc_nsfw_settings()
    new_headers = {
        "content-type": "application/grpc-web+proto",
        "x-grpc-web": "1",
        "origin": "https://grok.com",
        "referer": "https://grok.com/",
    }
    try:
        res = session.post(url, data=data, headers=new_headers, timeout=15)
        if log_callback:
            log_callback(
                f"[Debug] update_nsfw status: {res.status_code}, body: {response_preview(res)}"
            )
        if 200 <= res.status_code < 300:
            return True, "ok"
        if is_cloudflare_block_response(res):
            return (
                False,
                "update_nsfw_settings 被 grok.com 的 Cloudflare 防护拦截，HTTP "
                f"{res.status_code}",
            )
        return False, f"update_nsfw_settings HTTP {res.status_code}: {response_preview(res)}"
    except Exception as e:
        if log_callback:
            log_callback(f"[update_nsfw] 异常: {e}")
        return False, f"update_nsfw_settings 异常: {e}"


def enable_nsfw_for_token(token, cf_clearance="", user_agent="", log_callback=None):
    proxies = get_proxies()
    # cf_clearance 与签发它的浏览器 UA 严格绑定，优先用注册浏览器的真实 UA
    ua = user_agent or get_user_agent()
    try:
        with requests.Session(impersonate="chrome120", proxies=proxies) as session:
            cookie_parts = [f"sso={token}", f"sso-rw={token}"]
            if cf_clearance:
                cookie_parts.append(f"cf_clearance={cf_clearance}")
            session.headers.update(
                {
                    "user-agent": ua,
                    "cookie": "; ".join(cookie_parts),
                }
            )
            ok, message = set_tos_accepted(session, log_callback)
            if not ok:
                return False, message
            ok, message = set_birth_date(session, log_callback)
            if not ok:
                return False, message
            ok, message = update_nsfw_settings(session, log_callback)
            if not ok:
                return False, message
            return True, "成功开启 NSFW"
    except Exception as e:
        return False, f"异常: {str(e)}"


SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

browser = None
page = None
# Optional Playwright session attached to Roxy/local CDP for trusted human input.
_pw_runtime = None  # dict: playwright, browser, context, page, endpoint


def setup_light_theme(root):
    try:
        root.option_add("*Background", UI_BG)
        root.option_add("*Foreground", UI_FG)
        root.option_add("*selectBackground", UI_ACTIVE_BG)
        root.option_add("*selectForeground", UI_FG)
        root.option_add("*insertBackground", UI_FG)
        root.option_add("*Entry.Background", UI_ENTRY_BG)
        root.option_add("*Text.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Background", UI_ENTRY_BG)
        root.option_add("*Menu.Foreground", UI_FG)
        style = ttk.Style(root)
        available = set(style.theme_names())
        if "clam" in available:
            style.theme_use("clam")
        elif "default" in available:
            style.theme_use("default")
        root.configure(bg=UI_BG)
        style.configure(".", background=UI_BG, foreground=UI_FG, fieldbackground=UI_ENTRY_BG)
        style.configure("TFrame", background=UI_BG)
        style.configure("TLabelframe", background=UI_BG, foreground=UI_FG)
        style.configure("TLabelframe.Label", background=UI_BG, foreground=UI_FG)
        style.configure("TLabel", background=UI_BG, foreground=UI_FG)
        style.configure("TCheckbutton", background=UI_BG, foreground=UI_FG)
        style.configure("TButton", background=UI_BUTTON_BG, foreground=UI_FG)
        style.configure("TEntry", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TCombobox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
        style.configure("TSpinbox", fieldbackground=UI_ENTRY_BG, foreground=UI_FG)
    except Exception:
        pass


def tk_label(parent, text="", **kwargs):
    return tk.Label(parent, text=text, bg=kwargs.pop("bg", UI_BG), fg=kwargs.pop("fg", UI_FG), **kwargs)


def tk_entry(parent, textvariable=None, width=30, **kwargs):
    return tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        insertbackground=UI_FG,
        disabledbackground="#2f2f2f",
        disabledforeground=UI_MUTED_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
        **kwargs,
    )


def tk_button(parent, text="", command=None, state=tk.NORMAL, **kwargs):
    return tk.Button(
        parent,
        text=text,
        command=command,
        state=state,
        bg=UI_BUTTON_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        disabledforeground="#777777",
        relief=tk.RAISED,
        padx=10,
        pady=3,
        **kwargs,
    )


def tk_checkbutton(parent, text="", variable=None, **kwargs):
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        bg=UI_BG,
        fg=UI_FG,
        activebackground=UI_BG,
        activeforeground=UI_FG,
        selectcolor="#3d7be0",
        **kwargs,
    )


def tk_option_menu(parent, variable, values, width=12):
    menu = tk.OptionMenu(parent, variable, *values)
    menu.configure(
        width=width,
        bg=UI_ENTRY_BG,
        fg=UI_FG,
        activebackground=UI_ACTIVE_BG,
        activeforeground=UI_FG,
        highlightthickness=1,
        highlightbackground="#555555",
        relief=tk.SOLID,
    )
    menu["menu"].configure(bg=UI_ENTRY_BG, fg=UI_FG, activebackground=UI_ACTIVE_BG, activeforeground=UI_FG)
    return menu


def start_browser(log_callback=None):
    global browser, page
    driver = get_browser_driver()
    last_exc = None
    for attempt in range(1, 5):
        try:
            if driver == "browser_use":
                from browser_use_adapter import connect_browser_use

                if log_callback and attempt == 1:
                    country = str(config.get("browser_use_proxy_country", "") or "").strip() or "-"
                    use_proxy = bool(config.get("browser_use_use_proxy", True))
                    log_callback(
                        f"[*] 浏览器驱动: Browser Use Cloud | proxyCountry={country} | use_proxy={use_proxy}"
                    )
                browser = connect_browser_use(config, log_callback=log_callback)
                tabs = browser.get_tabs()
                page = tabs[-1] if tabs else browser.new_tab()
            elif driver == "roxy":
                from roxy_adapter import connect_roxy

                if log_callback and attempt == 1:
                    base = str(config.get("roxy_api_base") or "http://127.0.0.1:50000")
                    one = bool(config.get("roxy_one_profile_per_account", True))
                    log_callback(
                        f"[*] 浏览器驱动: RoxyBrowser | api={base} | "
                        f"one_profile_per_account={one}"
                    )
                browser = connect_roxy(config, log_callback=log_callback)
                tabs = browser.get_tabs()
                page = tabs[-1] if tabs else browser.new_tab()
            else:
                if log_callback and attempt == 1:
                    log_callback("[*] 浏览器驱动: 本地 Chromium (DrissionPage)")
                browser = Chromium(create_browser_options())
                tabs = browser.get_tabs()
                page = tabs[-1] if tabs else browser.new_tab()
                if log_callback and getattr(browser, "user_data_path", None):
                    log_callback(f"[Debug] 当前浏览器资料目录: {browser.user_data_path}")
            if log_callback and attempt > 1:
                log_callback(f"[*] 浏览器第 {attempt} 次启动成功")
            return browser, page
        except Exception as exc:
            last_exc = exc
            if log_callback:
                log_callback(f"[Debug] 浏览器启动失败(第{attempt}/4次): {exc}")
            try:
                if browser is not None:
                    browser.quit(del_data=True)
            except Exception:
                pass
            browser = None
            page = None
            time.sleep(min(1.5 * attempt, 4))
    raise Exception(f"浏览器启动失败，已重试4次: {last_exc}")


def stop_browser():
    global browser, page
    current = browser
    browser = None
    page = None
    _detach_playwright_cdp()
    if current is None:
        return
    try:
        current.quit(del_data=True)
    except BaseException:
        # KeyboardInterrupt 继承 BaseException，清理阶段必须吞掉，避免 Ctrl+C 刷 traceback
        pass


def restart_browser(log_callback=None):
    stop_browser()
    return start_browser(log_callback=log_callback)


def cleanup_runtime_memory(log_callback=None, reason="定期清理"):
    try:
        if log_callback:
            log_callback(f"[*] {reason}: 关闭浏览器并清理内存")
        stop_browser()
        collected = gc.collect()
        if log_callback:
            log_callback(f"[*] Python GC 已回收对象数: {collected}")
    except BaseException:
        # 退出清理中再收到 Ctrl+C 时静默结束，不向外抛
        try:
            stop_browser()
        except BaseException:
            pass


def _page_is_alive():
    """True if current page can still evaluate JS (CDP session not closed)."""
    global page
    if page is None:
        return False
    try:
        page.run_js("return 1;")
        return True
    except Exception:
        return False


def refresh_active_page(allow_restart=True):
    """Re-select the latest tab; optionally restart browser on hard disconnect.

    During SSO wait after Complete, navigation can briefly kill the CDP
    wrapper — restarting would wipe Roxy cookies. Prefer soft reattach.
    """
    global browser, page
    if browser is None:
        if allow_restart:
            restart_browser()
        return page
    try:
        tabs = browser.get_tabs()
        if tabs:
            page = tabs[-1]
        elif allow_restart:
            page = browser.new_tab()
        # Probe: Browser Use may keep a dead wrapper after remote disconnect
        if page is not None and not _page_is_alive():
            # Soft: try another tab before nuking the profile
            try:
                tabs = browser.get_tabs() or []
                for t in reversed(tabs):
                    page = t
                    if _page_is_alive():
                        return page
            except Exception:
                pass
            if allow_restart:
                raise RuntimeError("page closed")
            return page
    except Exception:
        if allow_restart:
            restart_browser()
        # else keep existing page handle (may still hold cookies)
    return page


def _collect_browser_cookies():
    """Best-effort cookie list even when page JS is flaky (post-navigation)."""
    global page, browser
    cookies = []
    # 1) active page
    try:
        if page is not None:
            cookies = page.cookies(all_domains=True, all_info=True) or []
            if cookies:
                return cookies
    except Exception:
        pass
    # 2) any tab
    try:
        if browser is not None:
            for t in browser.get_tabs() or []:
                try:
                    cookies = t.cookies(all_domains=True, all_info=True) or []
                    if cookies:
                        return cookies
                except Exception:
                    continue
    except Exception:
        pass
    # 3) CDP Network.getAllCookies
    try:
        if page is not None:
            res = page.run_cdp("Network.getAllCookies")
            if isinstance(res, dict):
                cookies = res.get("cookies") or []
                if cookies:
                    return cookies
    except Exception:
        pass
    return cookies or []


def extract_cf_clearance_and_ua(log_callback=None):
    """从注册浏览器提取 grok.com 的 cf_clearance 及其绑定的真实 UA。

    注册流程能拿到 sso 说明浏览器已通过 grok.com 的 Cloudflare 盾，
    此刻 cf_clearance 就在浏览器 cookie 里，配合真实 UA 可用于后续 NSFW 请求。

    返回:
      - (cf_clearance str, user_agent str)：任一取不到则为空字符串
    """
    cf_clearance = ""
    user_agent = ""
    try:
        active = refresh_active_page()
        if active is None:
            return "", ""
        cookies = active.cookies(all_domains=True, all_info=True) or []
        for item in cookies:
            if isinstance(item, dict):
                name = str(item.get("name", "")).strip()
                value = str(item.get("value", "")).strip()
            else:
                name = str(getattr(item, "name", "")).strip()
                value = str(getattr(item, "value", "")).strip()
            if name == "cf_clearance" and value:
                cf_clearance = value
                break
        try:
            ua = active.run_js("return navigator.userAgent;")
            if ua:
                user_agent = str(ua).strip()
        except Exception:
            pass
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 提取 cf_clearance 失败: {exc}")
    return cf_clearance, user_agent


def _email_input_visible():
    """True if signup email field is present and usable."""
    global page
    if page is None:
        return False
    try:
        return bool(
            page.run_js(
                r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const direct = Array.from(document.querySelectorAll(
  'input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i], input[placeholder*="メール"], input[aria-label*="メール"], input[placeholder*="邮箱"], input[aria-label*="邮箱"]'
));
const scored = Array.from(document.querySelectorAll('input, textarea')).filter((node) => {
  if (!isVisible(node) || node.disabled || node.readOnly) return false;
  const type = (node.getAttribute('type') || '').toLowerCase();
  if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'password'].includes(type)) return false;
  const meta = [
    node.getAttribute('placeholder'),
    node.getAttribute('aria-label'),
    node.getAttribute('name'),
    node.getAttribute('id'),
    node.getAttribute('autocomplete'),
    node.getAttribute('data-testid'),
  ].filter(Boolean).join(' ').toLowerCase();
  return (
    meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') ||
    meta.includes('邮箱') || meta.includes('郵件') || meta.includes('メール') ||
    meta.includes('이메일') || meta.includes('correo')
  );
});
const input = [...direct, ...scored].find((node) => isVisible(node) && !node.disabled && !node.readOnly);
return !!input;
                """
            )
        )
    except Exception:
        return False


# Email-signup CTA phrases across locales (x.ai / Browser Use geo often localizes UI).
# JP proxy → 「メールで登録」; CN → 「使用邮箱注册」; EN → 「Sign up with email」.
_EMAIL_SIGNUP_TEXT_SELECTORS = (
    "text:Sign up with email",
    "text:Sign up with Email",
    "text:Continue with email",
    "text:Use email",
    "text:使用邮箱注册",
    "text:用邮箱注册",
    "text:邮箱注册",
    "text:メールで登録",
    "text:メールアドレスで登録",
    "text:メールで続行",
    "text:이메일로 가입",
    "text:이메일로 계속",
    "text:Registrarse con correo",
    "text:S'inscrire avec e-mail",
    "text:Mit E-Mail registrieren",
    "xpath://button[contains(normalize-space(.), 'Sign up with email')]",
    "xpath://button[contains(normalize-space(.), '使用邮箱')]",
    "xpath://button[contains(normalize-space(.), 'メールで登録')]",
    "xpath://button[contains(normalize-space(.), 'メール')]",
    "xpath://*[@role='button' and contains(normalize-space(.), 'メールで登録')]",
    "xpath://*[@role='button' and contains(normalize-space(.), 'Sign up with email')]",
    "xpath://button[contains(translate(., 'EMAIL', 'email'), 'email') and (contains(translate(., 'SIGN', 'sign'), 'sign') or contains(translate(., 'CONTINUE', 'continue'), 'continue'))]",
)

# JS: score visible buttons for "sign up / continue with email" regardless of locale.
_FIND_EMAIL_SIGNUP_JS = r"""
function isVisible(node) {
  if (!node) return false;
  const s = getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
function textOf(node) {
  return [
    node.innerText,
    node.textContent,
    node.getAttribute('aria-label'),
    node.getAttribute('title'),
    node.getAttribute('data-testid'),
    node.getAttribute('name'),
  ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
// Prefer email/mail CTAs; exclude Apple / Google / X / login-only rows.
const mailHints = [
  'email', 'e-mail', 'mail', '邮箱', '郵件', '邮件', '電子郵件',
  'メール', 'メールアドレス', '이메일', 'correo', 'e-mail', 'courriel'
];
const signupHints = [
  'sign up', 'signup', 'register', 'continue', 'create', 'use',
  '注册', '註冊', '登録', '가입', 'registr', 'inscri', 'anmeld', 'contin'
];
const excludeHints = [
  'apple', 'google', 'github', 'facebook', 'microsoft', 'twitter',
  ' で登録', // keep mail path; X/Apple/Google handled below
];
const providerExclude = [
  'apple', 'google', 'x で', 'xで', 'with x', 'with twitter', 'with apple', 'with google',
  'apple で', 'google で', 'github', 'facebook',
  'appleで', 'googleで'
];
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'))
  .filter((n) => isVisible(n) && !n.disabled && n.getAttribute('aria-disabled') !== 'true');
let best = null;
let bestScore = 0;
let bestArea = Infinity;
const debug = [];
for (const node of nodes) {
  const raw = textOf(node);
  const t = raw.toLowerCase();
  if (!raw || raw.length > 100) continue;
  let score = 0;
  const hasMail = mailHints.some((h) => t.includes(h.toLowerCase()) || raw.includes(h));
  const hasSignup = signupHints.some((h) => t.includes(h.toLowerCase()) || raw.includes(h));
  if (hasMail) score += 5;
  if (hasSignup) score += 2;
  // Exact-ish JP/EN/CN labels
  if (raw.includes('メールで登録') || raw.includes('メールアドレスで登録')) score += 10;
  if (t.includes('sign up with email') || t.includes('continue with email')) score += 10;
  if (raw.includes('使用邮箱') || raw.includes('用邮箱注册')) score += 10;
  if (providerExclude.some((h) => t.includes(h) || raw.includes(h))) score -= 20;
  // Pure "login" without mail
  if ((t.includes('login') || raw.includes('ログイン') || raw.includes('登录')) && !hasMail) score -= 8;
  // Prefer leaf/smaller controls when scores tie (avoids wrapping div with doubled text)
  const r = node.getBoundingClientRect();
  const area = Math.max(1, r.width * r.height);
  if (score > bestScore || (score === bestScore && score >= 5 && area < bestArea)) {
    bestScore = score;
    best = node;
    bestArea = area;
  }
  if (score > 0) debug.push({ text: raw.slice(0, 40), score, area: Math.round(area) });
}
if (!best || bestScore < 5) {
  return { found: false, score: bestScore, candidates: debug.slice(0, 8) };
}
// Clear prior marks so only one target is live
document.querySelectorAll('[data-grok-email-signup="1"]').forEach((n) => n.removeAttribute('data-grok-email-signup'));
best.setAttribute('data-grok-email-signup', '1');
best.scrollIntoView({ block: 'center', inline: 'nearest' });
return { found: true, score: bestScore, text: textOf(best).slice(0, 60), area: Math.round(bestArea), candidates: debug.slice(0, 8) };
"""

_CLICK_MARKED_EMAIL_SIGNUP_JS = r"""
function isVisible(node) {
  if (!node) return false;
  const s = getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const btn = document.querySelector('[data-grok-email-signup="1"]');
if (!btn || !isVisible(btn)) return 'no-marked';
const rect = btn.getBoundingClientRect();
const x = rect.left + rect.width / 2;
const y = rect.top + rect.height / 2;
const opts = { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0 };
btn.scrollIntoView({ block: 'center', inline: 'nearest' });
btn.focus();
for (const type of ['pointerover','pointerenter','mouseover','mouseenter','pointerdown','mousedown','pointerup','mouseup','click']) {
  const Ctor = type.startsWith('pointer') ? PointerEvent : MouseEvent;
  btn.dispatchEvent(new Ctor(type, opts));
}
try { btn.click(); } catch (e) {}
return 'marked-click';
"""


def _find_email_signup_element():
    """Locate the email signup control (EN / JP / CN and other localized labels)."""
    global page
    if page is None:
        return None

    # 1) Mark best CTA via locale-aware JS, then resolve element
    try:
        found = page.run_js(_FIND_EMAIL_SIGNUP_JS)
        if isinstance(found, dict) and found.get("found"):
            ele = page.ele("@data-grok-email-signup=1", timeout=0.6)
            if ele:
                return ele
    except Exception:
        pass

    # 2) Explicit text / xpath selectors (localized)
    for sel in _EMAIL_SIGNUP_TEXT_SELECTORS:
        try:
            ele = page.ele(sel, timeout=0.35)
            if ele:
                return ele
        except Exception:
            continue

    # 3) Scan buttons by text heuristics
    try:
        for ele in page.eles("tag:button", timeout=0.5) or []:
            try:
                text = " ".join(str(ele.text or "").split())
            except Exception:
                continue
            low = text.lower()
            if any(x in text for x in ("メールで登録", "メールアドレスで登録", "使用邮箱", "用邮箱")):
                return ele
            if "email" in low and any(x in low for x in ("sign", "continue", "use", "register")):
                return ele
            if "邮箱" in text and ("注册" in text or "註冊" in text or "注册" in text):
                return ele
            if "メール" in text and ("登録" in text or "続行" in text):
                return ele
    except Exception:
        pass
    return None


def _cdp_activate_page():
    """Best-effort bring tab to front so CDP Input hits the visible surface."""
    global page
    if page is None:
        return
    for cmd, kwargs in (
        ("Page.bringToFront", {}),
        ("Page.enable", {}),
        ("Runtime.enable", {}),
        ("DOM.enable", {}),
        ("Input.setIgnoreInputEvents", {"ignore": False}),
    ):
        try:
            page.run_cdp(cmd, **kwargs)
        except Exception:
            pass


def _roxy_cdp_http_endpoint():
    """http://host:port for Playwright connect_over_cdp from Roxy open result."""
    global browser
    if browser is None:
        return None
    addr = getattr(browser, "roxy_debugger_address", None) or ""
    addr = str(addr).strip()
    if not addr:
        return None
    if addr.startswith("ws://") or addr.startswith("wss://"):
        # ws://127.0.0.1:9222/devtools/... → http://127.0.0.1:9222
        hostport = addr.split("://", 1)[1].split("/", 1)[0]
        return f"http://{hostport}"
    if addr.startswith("http://") or addr.startswith("https://"):
        return addr
    return f"http://{addr}"


def _detach_playwright_cdp():
    """Disconnect Playwright only (do not close the Roxy profile)."""
    global _pw_runtime
    rt = _pw_runtime
    _pw_runtime = None
    if not rt:
        return
    try:
        b = rt.get("browser")
        if b is not None:
            # disconnect only — remote browser stays open under Roxy/Drission
            b.close()
    except Exception:
        pass
    try:
        pw = rt.get("playwright")
        if pw is not None:
            pw.stop()
    except Exception:
        pass


def _attach_playwright_cdp(log_callback=None):
    """
    Attach Playwright to the same Chromium Roxy already opened.
    Playwright's mouse/keyboard goes through CDP Input with full event fidelity
    (isTrusted path sites expect) — better than hand-rolled dispatchMouseEvent.
    """
    global _pw_runtime, page
    endpoint = _roxy_cdp_http_endpoint()
    if not endpoint:
        return None
    if _pw_runtime and _pw_runtime.get("endpoint") == endpoint:
        pw_page = _pw_runtime.get("page")
        if pw_page is not None:
            try:
                # keep URL in sync with Drission tab when possible
                if page is not None and getattr(page, "url", None):
                    if pw_page.url.rstrip("/") != str(page.url).rstrip("/"):
                        # Prefer matching existing page by URL rather than navigating away
                        browser_pw = _pw_runtime.get("browser")
                        matched = None
                        if browser_pw is not None:
                            for ctx in browser_pw.contexts:
                                for p in ctx.pages:
                                    try:
                                        if str(page.url).split("?")[0] in (p.url or ""):
                                            matched = p
                                            break
                                    except Exception:
                                        continue
                                if matched:
                                    break
                        if matched is not None:
                            _pw_runtime["page"] = matched
                            pw_page = matched
                return pw_page
            except Exception:
                _detach_playwright_cdp()
    _detach_playwright_cdp()
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] Playwright 不可用: {exc}")
        return None
    try:
        pw = sync_playwright().start()
        browser_pw = pw.chromium.connect_over_cdp(endpoint)
        # Pick the page that matches current Drission URL / accounts.x.ai
        target_url = ""
        try:
            if page is not None:
                target_url = str(page.url or "")
        except Exception:
            target_url = ""
        chosen = None
        for ctx in browser_pw.contexts:
            for p in ctx.pages:
                u = p.url or ""
                if target_url and target_url.split("?")[0] in u:
                    chosen = p
                    break
                if "accounts.x.ai" in u or "x.ai" in u:
                    chosen = p
            if chosen:
                break
        if chosen is None:
            for ctx in browser_pw.contexts:
                if ctx.pages:
                    chosen = ctx.pages[0]
                    break
        if chosen is None:
            ctx = browser_pw.contexts[0] if browser_pw.contexts else browser_pw.new_context()
            chosen = ctx.new_page()
        _pw_runtime = {
            "playwright": pw,
            "browser": browser_pw,
            "context": chosen.context,
            "page": chosen,
            "endpoint": endpoint,
        }
        if log_callback:
            log_callback(f"[Debug] Playwright 已附着 CDP {endpoint} url={chosen.url[:80]}")
        return chosen
    except Exception as exc:
        _detach_playwright_cdp()
        if log_callback:
            log_callback(f"[Debug] Playwright 附着失败: {exc}")
        return None


def _playwright_human_click_selector(selector: str, log_callback=None, timeout_ms: int = 4000):
    """Real Playwright locator click (CDP mouse under the hood)."""
    pw_page = _attach_playwright_cdp(log_callback=log_callback)
    if pw_page is None:
        raise RuntimeError("no-pw")
    loc = pw_page.locator(selector).first
    loc.wait_for(state="visible", timeout=timeout_ms)
    loc.scroll_into_view_if_needed(timeout=timeout_ms)
    box = loc.bounding_box()
    if not box:
        raise RuntimeError("no-box")
    # Human-ish: move to a jittered point inside the control, then click
    jx = box["x"] + box["width"] * random.uniform(0.35, 0.65)
    jy = box["y"] + box["height"] * random.uniform(0.35, 0.65)
    pw_page.mouse.move(jx, jy, steps=random.randint(8, 16))
    time.sleep(random.uniform(0.04, 0.12))
    pw_page.mouse.down()
    time.sleep(random.uniform(0.04, 0.09))
    pw_page.mouse.up()
    return f"pw-mouse:{int(jx)},{int(jy)}"


def _playwright_human_click_role_text(texts, log_callback=None):
    """Click by accessible name via Playwright (handles nested label text)."""
    pw_page = _attach_playwright_cdp(log_callback=log_callback)
    if pw_page is None:
        raise RuntimeError("no-pw")
    for text in texts:
        try:
            loc = pw_page.get_by_role(
                "button", name=re.compile(re.escape(text), re.I)
            ).first
            if loc.count() == 0:
                loc = pw_page.get_by_text(text, exact=False).first
            if loc.count() == 0:
                continue
            if not loc.is_visible(timeout=500):
                continue
            name = ""
            try:
                name = (loc.inner_text(timeout=400) or "").strip().lower()
            except Exception:
                pass
            # Skip wrong OAuth rows if we matched broadly
            if any(p in name for p in ("apple", "google", "github", "with x")) and "email" not in name:
                continue
            loc.scroll_into_view_if_needed(timeout=2500)
            # Full pointer path: move → hover → down → up (some React handlers
            # ignore bare click() without prior pointerover/pointerdown).
            box = loc.bounding_box()
            if box:
                jx = box["x"] + box["width"] * random.uniform(0.35, 0.65)
                jy = box["y"] + box["height"] * random.uniform(0.35, 0.65)
                # Approach from slightly above
                pw_page.mouse.move(jx, max(0, jy - 40), steps=random.randint(4, 8))
                time.sleep(random.uniform(0.03, 0.08))
                pw_page.mouse.move(jx, jy, steps=random.randint(6, 12))
                time.sleep(random.uniform(0.06, 0.16))
                try:
                    loc.hover(timeout=1500)
                except Exception:
                    pass
                time.sleep(random.uniform(0.04, 0.1))
                pw_page.mouse.down()
                time.sleep(random.uniform(0.05, 0.12))
                pw_page.mouse.up()
            else:
                loc.click(timeout=3000, delay=random.randint(40, 90), force=True)
            if log_callback:
                log_callback(f"[Debug] Playwright human click: {text!r}")
            return f"pw-role:{text}"
        except Exception:
            continue
    raise RuntimeError("pw-role-miss")


def _cdp_user_gesture_click_selector(selector: str, fallback_find_js: str = ""):
    """
    Click via CDP Runtime.evaluate with userGesture=true.

    Note: DrissionPage run_js already passes userGesture=true; this path is kept
    as an explicit evaluate. Real isTrusted input still needs Input.* / Playwright.
    """
    global page
    if page is None:
        raise RuntimeError("no page")
    _cdp_activate_page()
    # Prefer marked selector; optional JS finder if mark missing
    expression = f"""
(() => {{
  let btn = document.querySelector({json.dumps(selector)});
  if (!btn) {{
    {fallback_find_js or '/* no fallback finder */'}
  }}
  if (!btn) return 'no-btn';
  try {{ btn.scrollIntoView({{block:'center', inline:'nearest'}}); }} catch (e) {{}}
  try {{ btn.focus({{preventScroll:true}}); }} catch (e) {{ try {{ btn.focus(); }} catch (e2) {{}} }}
  btn.click();
  return 'userGesture-click';
}})()
"""
    result = page.run_cdp(
        "Runtime.evaluate",
        expression=expression,
        userGesture=True,
        awaitPromise=False,
        returnByValue=True,
    )
    value = None
    if isinstance(result, dict):
        value = (result.get("result") or {}).get("value")
        if value is None:
            value = result.get("value")
    return str(value or result)


def _cdp_key_activate_focused(key: str = "Enter"):
    """Dispatch trusted keydown/keypress/keyup via CDP Input (Enter/Space on focused CTA)."""
    global page
    if page is None:
        raise RuntimeError("no page")
    _cdp_activate_page()
    key = key if key in ("Enter", " ", "Space") else "Enter"
    if key == "Space":
        key = " "
    vk = 13 if key == "Enter" else 32
    code = "Enter" if key == "Enter" else "Space"
    text = "\r" if key == "Enter" else " "
    for phase, extras in (
        ("keyDown", {"text": text}),
        ("keyUp", {}),
    ):
        payload = {
            "type": phase,
            "windowsVirtualKeyCode": vk,
            "code": code,
            "key": "Enter" if key == "Enter" else " ",
            "nativeVirtualKeyCode": vk,
        }
        if phase == "keyDown":
            payload["text"] = text
        page.run_cdp("Input.dispatchKeyEvent", **payload)
        time.sleep(0.03)
    return f"cdp-key:{code}"


def _cdp_mouse_click_at(x, y, steps: int = 8, buttons_mask: int = 1):
    """Trusted CDP mouse move + left click with modifiers browsers expect."""
    global page
    if page is None:
        raise RuntimeError("no page")
    _cdp_activate_page()
    x = float(x)
    y = float(y)
    # Gentle cursor approach from a nearby random start
    try:
        sx = max(8.0, x + random.uniform(-80, -20))
        sy = max(8.0, y + random.uniform(-40, 40))
        n = max(2, steps)
        for i in range(1, n + 1):
            # ease-out
            t = i / n
            ease = 1 - (1 - t) * (1 - t)
            xi = sx + (x - sx) * ease
            yi = sy + (y - sy) * ease
            page.run_cdp(
                "Input.dispatchMouseEvent",
                type="mouseMoved",
                x=xi,
                y=yi,
                modifiers=0,
                button="none",
                buttons=0,
                pointerType="mouse",
            )
            time.sleep(random.uniform(0.012, 0.028))
    except Exception:
        pass
    time.sleep(random.uniform(0.04, 0.1))
    # Full pointer-ish sequence: move → pressed → released (clickCount)
    for etype, btns, click_count in (
        ("mousePressed", buttons_mask, 1),
        ("mouseReleased", 0, 1),
    ):
        page.run_cdp(
            "Input.dispatchMouseEvent",
            type=etype,
            x=x,
            y=y,
            button="left",
            buttons=btns,
            clickCount=click_count,
            modifiers=0,
            pointerType="mouse",
        )
        time.sleep(random.uniform(0.045, 0.08))
    return f"cdp-click:{int(x)},{int(y)}"


def _cdp_touch_tap_at(x, y):
    """Touch path — some React handlers accept touch when mouse is filtered."""
    global page
    if page is None:
        raise RuntimeError("no page")
    _cdp_activate_page()
    x, y = float(x), float(y)
    try:
        page.run_cdp(
            "Input.dispatchTouchEvent",
            type="touchStart",
            touchPoints=[{"x": x, "y": y, "radiusX": 2, "radiusY": 2, "force": 0.5, "id": 0}],
        )
        time.sleep(0.05)
        page.run_cdp(
            "Input.dispatchTouchEvent",
            type="touchEnd",
            touchPoints=[],
        )
        return f"cdp-touch:{int(x)},{int(y)}"
    except Exception as exc:
        raise RuntimeError(f"touch-fail:{type(exc).__name__}") from exc


def _email_signup_click_point():
    """
    Return click coordinates for the email CTA using elementFromPoint hit-testing.

    Nested labels double the text ('Sign up with email Sign up with email');
    we mark the outermost interactive node and sample a free point inside it.
    """
    global page
    if page is None:
        return None
    try:
        point = page.run_js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
function isEmailCtaText(t) {
  t = (t || '').replace(/\s+/g, ' ').trim().toLowerCase();
  if (!t || t.length > 100) return false;
  if (/apple|google|github|facebook|with x\b|with twitter/.test(t) && !t.includes('email')) return false;
  return (
    t.includes('sign up with email') || t.includes('continue with email') ||
    t.includes('メールで登録') || t.includes('メールアドレスで登録') ||
    t.includes('使用邮箱') || t.includes('用邮箱注册') ||
    (t.includes('email') && (t.includes('sign') || t.includes('continue') || t.includes('use')))
  );
}
function interactiveRoot(node) {
  let n = node;
  while (n && n !== document.body) {
    if (n.matches && n.matches('button, a, [role="button"], input[type="button"], input[type="submit"]')) return n;
    n = n.parentElement;
  }
  return node;
}
let btn = document.querySelector('[data-grok-email-signup="1"]');
if (!btn || !isVisible(btn)) {
  // Prefer the *smallest* matching control (leaf button, not a wrapping div that also has text)
  const cands = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter(isVisible)
    .filter((n) => isEmailCtaText((n.innerText || n.textContent || '') + ' ' + (n.getAttribute('aria-label') || '')))
    .map((n) => {
      const r = n.getBoundingClientRect();
      return { n, area: r.width * r.height, r };
    })
    .sort((a, b) => a.area - b.area);
  btn = cands.length ? cands[0].n : null;
  if (btn) btn.setAttribute('data-grok-email-signup', '1');
}
if (!btn) return null;
btn = interactiveRoot(btn);
btn.setAttribute('data-grok-email-signup', '1');
try { btn.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
const r = btn.getBoundingClientRect();
if (r.width <= 0 || r.height <= 0) return null;
// Sample several points; pick one where elementFromPoint resolves to our button tree
const samples = [
  [0.5, 0.5], [0.4, 0.5], [0.6, 0.5], [0.5, 0.4], [0.5, 0.6],
  [0.3, 0.5], [0.7, 0.5], [0.5, 0.35], [0.5, 0.65],
];
let hitX = r.left + r.width / 2;
let hitY = r.top + r.height / 2;
let hitTag = '';
for (const [fx, fy] of samples) {
  const x = r.left + r.width * fx;
  const y = r.top + r.height * fy;
  const top = document.elementFromPoint(x, y);
  if (!top) continue;
  if (btn === top || btn.contains(top) || (top.contains && top.contains(btn))) {
    hitX = x; hitY = y; hitTag = (top.tagName || '') + (top.className ? '.' + String(top.className).slice(0, 24) : '');
    break;
  }
}
// Clear sticky hover overlays if any full-page catcher sits above
const topNow = document.elementFromPoint(hitX, hitY);
const blocked = topNow && !(btn === topNow || btn.contains(topNow));
return {
  x: hitX,
  y: hitY,
  width: r.width,
  height: r.height,
  text: (btn.innerText || btn.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60),
  hit: hitTag,
  blocked: !!blocked,
  topTag: topNow ? (topNow.tagName + ':' + (topNow.id || topNow.className || '').toString().slice(0, 40)) : null,
};
            """
        )
        if isinstance(point, dict) and point.get("x") is not None and point.get("y") is not None:
            return point
    except Exception:
        pass
    return None


def _complete_signup_click_point():
    """Center of final-step 'Complete sign up' / create-account button."""
    global page
    if page is None:
        return None
    try:
        point = page.run_js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
function buttonText(node) {
  return [
    node.innerText, node.textContent, node.getAttribute('value'),
    node.getAttribute('aria-label'), node.getAttribute('title'),
  ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll(
  'button[type="submit"], button, [role="button"], input[type="submit"]'
)).filter((n) => isVisible(n) && !n.disabled && n.getAttribute('aria-disabled') !== 'true');
const submitBtn = buttons.find((node) => {
  const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
  if (t.includes('email') && !t.includes('complete')) return false;
  if (t.includes('goback') || t.includes('go back') || t.includes('返回') || t.includes('戻る')) return false;
  return (
    t.includes('completesignup') || t.includes('completeyoursignup') ||
    t.includes('complete sign up') || t.includes('createaccount') ||
    t.includes('create account') || t.includes('createyouraccount') ||
    t.includes('完成注册') || t.includes('创建账户') || t.includes('创建帐户') ||
    t.includes('登録を完了') || t.includes('登録完了') || t.includes('アカウントを作成') ||
    t.includes('登録する') ||
    (t.includes('sign up') && !t.includes('with')) ||
    t.includes('submit') || t.includes('continue') || t.includes('next') ||
    t.includes('続行') || t.includes('続ける') || t.includes('完了')
  );
}) || buttons.find((node) => node.getAttribute('type') === 'submit');
if (!submitBtn) return null;
submitBtn.setAttribute('data-grok-complete-signup', '1');
submitBtn.scrollIntoView({ block: 'center', inline: 'nearest' });
const r = submitBtn.getBoundingClientRect();
if (r.width <= 0 || r.height <= 0) return null;
return {
  x: r.left + r.width / 2,
  y: r.top + r.height / 2,
  text: buttonText(submitBtn).slice(0, 60),
};
            """
        )
        if isinstance(point, dict) and point.get("x") is not None and point.get("y") is not None:
            return point
    except Exception:
        pass
    return None


def _cdp_click_complete_signup(log_callback=None):
    """Trusted click on final Complete sign up (Playwright / CDP mouse / keys)."""
    # 1) Playwright human mouse (same Roxy CDP)
    try:
        detail = _playwright_human_click_role_text(
            [
                "Complete sign up",
                "Complete signup",
                "Complete your sign up",
                "Create account",
                "Create your account",
                "完成注册",
                "创建账户",
                "登録を完了",
                "アカウントを作成",
            ],
            log_callback=log_callback,
        )
        if detail and "miss" not in detail:
            return f"cdp-complete:{detail}"
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] Playwright 最终提交失败: {exc}")
    try:
        detail = _playwright_human_click_selector(
            '[data-grok-complete-signup="1"]', log_callback=log_callback
        )
        return f"cdp-complete:{detail}"
    except Exception:
        pass

    # 2) CDP coordinate mouse
    point = _complete_signup_click_point()
    if point:
        try:
            detail = _cdp_mouse_click_at(point["x"], point["y"], steps=random.randint(5, 10))
            if log_callback:
                log_callback(
                    f"[Debug] CDP 最终提交点击: text={point.get('text')!r} {detail}"
                )
            return f"cdp-complete:{detail}:{point.get('text') or ''}"
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] CDP 最终提交失败: {exc}")

    # 3) Focus + Enter
    try:
        page.run_cdp(
            "Runtime.evaluate",
            expression="""
(() => {
  const btn = document.querySelector('[data-grok-complete-signup="1"]');
  if (btn) { btn.focus({preventScroll:true}); return 'focused'; }
  return 'no-btn';
})()
""",
            userGesture=True,
            returnByValue=True,
        )
        return f"cdp-complete:{_cdp_key_activate_focused('Enter')}"
    except Exception:
        pass

    # 4) userGesture evaluate click
    try:
        ug = _cdp_user_gesture_click_selector(
            '[data-grok-complete-signup="1"]',
            fallback_find_js=r"""
    btn = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
      .find((n) => {
        const t = ((n.innerText || n.textContent || '') + ' ' + (n.getAttribute('aria-label') || ''))
          .replace(/\s+/g, ' ').trim().toLowerCase();
        if (!t || t.length > 80) return false;
        if (t.includes('email') && !t.includes('complete')) return false;
        if (t.includes('go back') || t.includes('返回') || t.includes('戻る')) return false;
        return (
          t.includes('complete sign up') || t.includes('complete signup') ||
          t.includes('create account') || t.includes('完成注册') || t.includes('创建账户') ||
          t.includes('登録を完了') || t.includes('アカウントを作成') ||
          (t.includes('sign up') && !t.includes('with')) ||
          t.includes('submit') || t.includes('continue')
        );
      });
    if (btn) btn.setAttribute('data-grok-complete-signup', '1');
""",
        )
        if ug and "no-btn" not in str(ug):
            if log_callback:
                log_callback(f"[Debug] CDP userGesture 最终提交: {ug}")
            return f"cdp-complete:{ug}"
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] userGesture 最终提交失败: {exc}")
    return "cdp-complete:no-btn"


def _native_click_email_signup(log_callback=None):
    """
    Click email signup with strategies that React/Next actually honor.

    Honest stack (most → least human):
      1. Playwright over Roxy CDP — full mouse path (best isTrusted fidelity)
      2. CDP Input.dispatchMouseEvent at elementFromPoint hit coords
      3. CDP keyboard Enter/Space after focus (alternate activation)
      4. CDP touch tap
      5. Runtime.evaluate userGesture + btn.click / Drission native / JS fallbacks

    Note: DrissionPage run_js already sets userGesture=true; isTrusted still
    requires Input.* / Playwright. Locale: JP often 「メールで登録」.
    """
    global page

    # Always run finder JS first so we log which localized label we hit
    find_info = None
    try:
        find_info = page.run_js(_FIND_EMAIL_SIGNUP_JS)
        if log_callback and isinstance(find_info, dict):
            if find_info.get("found"):
                log_callback(
                    f"[Debug] 定位邮箱注册按钮: text={find_info.get('text')!r} score={find_info.get('score')}"
                )
            else:
                cands = find_info.get("candidates") or []
                log_callback(f"[Debug] 未定位邮箱注册按钮 candidates={cands}")
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 邮箱注册按钮扫描失败: {exc}")

    ele = _find_email_signup_element()
    strategies = []
    point = _email_signup_click_point()
    if log_callback and isinstance(point, dict):
        log_callback(
            f"[Debug] CTA hit point: xy=({point.get('x'):.0f},{point.get('y'):.0f}) "
            f"blocked={point.get('blocked')} top={point.get('topTag')!r} text={point.get('text')!r}"
        )

    _EMAIL_CTA_FINDER_FALLBACK = r"""
    const cands = Array.from(document.querySelectorAll('button, a, [role="button"]')).filter((n) => {
      const t = ((n.innerText || n.textContent || '') + ' ' + (n.getAttribute('aria-label') || ''))
        .replace(/\s+/g, ' ').trim().toLowerCase();
      if (!t || t.length > 100) return false;
      if (/apple|google|github|facebook|with x\b|with twitter/.test(t) && !t.includes('email')) return false;
      return (
        t.includes('sign up with email') || t.includes('continue with email') ||
        t.includes('メールで登録') || t.includes('メールアドレスで登録') ||
        t.includes('使用邮箱') || t.includes('用邮箱注册') ||
        (t.includes('email') && (t.includes('sign') || t.includes('continue') || t.includes('use')))
      );
    });
    // Prefer smallest matching node (leaf button)
    cands.sort((a, b) => {
      const ra = a.getBoundingClientRect(), rb = b.getBoundingClientRect();
      return (ra.width * ra.height) - (rb.width * rb.height);
    });
    btn = cands[0] || null;
    if (btn) btn.setAttribute('data-grok-email-signup', '1');
"""

    _EMAIL_ROLE_TEXTS = (
        "Sign up with email",
        "Continue with email",
        "Use email",
        "メールで登録",
        "メールアドレスで登録",
        "使用邮箱注册",
        "用邮箱注册",
    )

    def try_playwright_role():
        return _playwright_human_click_role_text(_EMAIL_ROLE_TEXTS, log_callback=log_callback)

    def try_playwright_selector():
        return _playwright_human_click_selector(
            '[data-grok-email-signup="1"]', log_callback=log_callback
        )

    def try_cdp_coords():
        pt = point or _email_signup_click_point()
        if not pt:
            raise RuntimeError("no-click-point")
        if pt.get("blocked") and log_callback:
            log_callback(f"[Debug] CTA 可能被遮挡 top={pt.get('topTag')!r}，仍尝试 CDP 点击")
        return _cdp_mouse_click_at(pt["x"], pt["y"], steps=random.randint(6, 12))

    def try_cdp_touch():
        pt = point or _email_signup_click_point()
        if not pt:
            raise RuntimeError("no-click-point")
        return _cdp_touch_tap_at(pt["x"], pt["y"])

    def try_focus_enter():
        # Focus via userGesture evaluate, then trusted Enter/Space
        _cdp_user_gesture_click_selector(
            '[data-grok-email-signup="1"]',
            fallback_find_js=_EMAIL_CTA_FINDER_FALLBACK,
        )
        # Re-focus without relying on click side-effects
        try:
            page.run_cdp(
                "Runtime.evaluate",
                expression="""
(() => {
  const btn = document.querySelector('[data-grok-email-signup="1"]');
  if (!btn) return 'no-btn';
  btn.focus({preventScroll:true});
  return 'focused';
})()
""",
                userGesture=True,
                returnByValue=True,
            )
        except Exception:
            pass
        detail = _cdp_key_activate_focused("Enter")
        time.sleep(0.15)
        try:
            _cdp_key_activate_focused(" ")
        except Exception:
            pass
        return detail

    def try_user_gesture():
        return _cdp_user_gesture_click_selector(
            '[data-grok-email-signup="1"]',
            fallback_find_js=_EMAIL_CTA_FINDER_FALLBACK,
        )

    def try_marked_js():
        return page.run_js(_CLICK_MARKED_EMAIL_SIGNUP_JS)

    # Keep the first pass short — if SPA is dead, refresh beats 10 click strategies.
    ordered = [
        ("pw-role", try_playwright_role),
        ("cdp", try_cdp_coords),
        ("userGesture", try_user_gesture),
    ]

    if ele is not None:
        try:
            ele.scroll.to_see()
        except Exception:
            try:
                page.run_js(
                    "arguments[0].scrollIntoView({block:'center', inline:'nearest'});",
                    ele,
                )
            except Exception:
                pass
        time.sleep(0.15)

        def try_dp_click():
            ele.click(by_js=False)
            return "dp-click"

        ordered.append(("native", try_dp_click))

    for name, fn in ordered:
        try:
            result = fn()
            strategies.append(f"{name}:{result}")
            time.sleep(0.9)
            if _email_input_visible():
                return True, ",".join(strategies)
        except Exception as exc:
            strategies.append(f"{name}:err:{type(exc).__name__}")
            if log_callback:
                log_callback(f"[Debug] 邮箱注册点击策略 {name} 失败: {exc}")

    return False, ",".join(strategies) or "all-failed"


def _wait_signup_cta_ready(timeout=12, cancel_callback=None, min_scripts=20):
    """Wait until email-signup CTA is visible and page scripts look loaded."""
    global page
    if page is None:
        return False
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            last = page.run_js(
                r"""
function isVisible(node) {
  if (!node) return false;
  const s = getComputedStyle(node);
  if (s.display==='none'||s.visibility==='hidden'||s.opacity==='0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
const btn = Array.from(document.querySelectorAll('button,a,[role="button"]'))
  .filter(isVisible)
  .find((n) => /sign up with email|メールで登録|使用邮箱|continue with email/i
    .test((n.innerText||n.textContent||'') + ' ' + (n.getAttribute('aria-label')||'')));
return {
  ready: document.readyState,
  hasCta: !!btn,
  scripts: document.scripts.length,
};
                """
            )
            if (
                isinstance(last, dict)
                and last.get("hasCta")
                and int(last.get("scripts") or 0) >= min_scripts
            ):
                return True
        except Exception:
            pass
        sleep_with_cancel(0.35, cancel_callback)
    return bool(isinstance(last, dict) and last.get("hasCta"))


def _reload_signup_page(log_callback=None, cancel_callback=None, reason=""):
    """
    Hard reload accounts.x.ai sign-up.

    First paint under Roxy often shows CTAs with no working handlers (even manual
    click fails). A full refresh rehydrates the SPA and makes the same buttons work.
    """
    global page, browser
    if page is None and browser is not None:
        try:
            page = browser.get_tab(0)
        except Exception:
            page = None
    if page is None:
        raise RuntimeError("no page for signup reload")
    if log_callback:
        msg = "[*] 刷新注册页以修复首次加载的死按钮"
        if reason:
            msg += f"（{reason}）"
        log_callback(msg)
    try:
        # Prefer true reload of current document when already on sign-up
        on_signup = False
        try:
            on_signup = "accounts.x.ai" in str(page.url or "") and "sign-up" in str(
                page.url or ""
            )
        except Exception:
            on_signup = False
        if on_signup:
            try:
                page.run_cdp("Page.reload", ignoreCache=True)
            except Exception:
                try:
                    page.run_js("location.reload(true);")
                except Exception:
                    page.get(SIGNUP_URL, timeout=45)
        else:
            page.get(SIGNUP_URL, timeout=45)
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] reload 异常，改走 get: {exc}")
        page.get(SIGNUP_URL, timeout=45)
    try:
        page.wait.doc_loaded(timeout=12, raise_err=False)
    except Exception:
        pass
    try:
        _cdp_activate_page()
    except Exception:
        pass
    _wait_signup_cta_ready(timeout=8, cancel_callback=cancel_callback)
    # Short beat after CTA paint — don't over-wait; dead pages need another reload not sleep
    sleep_with_cancel(0.8, cancel_callback)
    if log_callback:
        try:
            ready = page.run_js("return document.readyState;")
        except Exception:
            ready = "?"
        log_callback(f"[*] 刷新后 URL: {page.url} readyState={ready}")


def click_email_signup_button(timeout=60, log_callback=None, cancel_callback=None):
    """Click email signup and only return after the email input is visible."""
    global page
    # Already on email step
    if _email_input_visible():
        if log_callback:
            log_callback("[*] 邮箱输入框已存在，跳过入口点击")
        return True

    deadline = time.time() + timeout
    last_diag = 0.0
    attempt = 0
    reloads_done = 0
    max_reloads = 4  # dead SPA is common — refresh quickly, don't burn strategies
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        attempt += 1
        if log_callback:
            log_callback(
                f"[Debug] 尝试点击邮箱注册入口 (#{attempt})，"
                "需出现邮箱输入框才算成功（支持 EN/JP/CN 等本地化文案）..."
            )

        ok, detail = _native_click_email_signup(log_callback=log_callback)
        if ok:
            if log_callback:
                log_callback(f"[*] 已进入邮箱注册步骤 ({detail})")
            return True

        # Short post-click window — if SPA is dead, refresh immediately
        wait_until = time.time() + 1.2
        while time.time() < wait_until:
            raise_if_cancelled(cancel_callback)
            if _email_input_visible():
                if log_callback:
                    log_callback(f"[*] 已进入邮箱注册步骤 (delayed, {detail})")
                return True
            sleep_with_cancel(0.25, cancel_callback)

        if log_callback and time.time() - last_diag >= 2:
            last_diag = time.time()
            try:
                visible = page.run_js(
                    r"""
function isVisible(node) {
  if (!node) return false;
  const s = getComputedStyle(node);
  if (s.display==='none'||s.visibility==='hidden'||s.opacity==='0') return false;
  const r = node.getBoundingClientRect();
  return r.width>0 && r.height>0;
}
return Array.from(document.querySelectorAll('button, a, [role="button"]'))
  .filter(isVisible)
  .map(n => (n.innerText||n.textContent||'').replace(/\s+/g,' ').trim())
  .filter(Boolean)
  .slice(0, 8);
                    """
                )
            except Exception:
                visible = []
            log_callback(
                f"[Debug] 点击后仍无邮箱框 url={page.url if page else ''}; "
                f"strategy={detail}; buttons={' | '.join(visible) if visible else 'none'}"
            )

        # Immediately hard-refresh when click doesn't advance (don't wait long)
        if reloads_done < max_reloads:
            reloads_done += 1
            try:
                _reload_signup_page(
                    log_callback=log_callback,
                    cancel_callback=cancel_callback,
                    reason=f"入口未前进，立即硬刷新 {reloads_done}/{max_reloads}",
                )
            except Exception as exc:
                if log_callback:
                    log_callback(f"[Debug] 注册页刷新失败: {exc}")
            continue

        sleep_with_cancel(0.5, cancel_callback)

    if log_callback:
        page_html = page.html[:500] if page else "no page"
        log_callback(f"[Debug] 页面内容片段: {page_html}")

    raise Exception(
        "未进入邮箱注册步骤（邮箱注册按钮点击无效；"
        "若使用 Browser Use 且 country=jp，按钮文案可能是「メールで登録」——已支持本地化匹配，请重试）"
    )


def open_signup_page(log_callback=None, cancel_callback=None):
    global browser, page
    raise_if_cancelled(cancel_callback)
    if browser is None:
        start_browser()
        if log_callback:
            log_callback("[*] 浏览器已启动")
    try:
        page = browser.get_tab(0)
        if log_callback:
            log_callback(f"[*] 正在打开注册页: {SIGNUP_URL}")
        # timeout bounds navigation even if some assets never finish
        page.get(SIGNUP_URL, timeout=45)
    except Exception as e:
        if log_callback:
            log_callback(f"[Debug] 打开URL异常: {e}")
        try:
            page = browser.new_tab(SIGNUP_URL)
        except Exception as e2:
            if log_callback:
                log_callback(f"[Debug] 创建新标签页异常: {e2}")
            restart_browser()
            page = browser.new_tab(SIGNUP_URL)
    # Do not block on full network idle. SPA pages often keep the tab spinner
    # running; proceed once DOM is ready (or after a short cap).
    try:
        page.wait.doc_loaded(timeout=10, raise_err=False)
    except Exception as wait_exc:
        if log_callback:
            log_callback(f"[Debug] doc_loaded 等待结束/跳过: {wait_exc}")
    try:
        _cdp_activate_page()
    except Exception:
        pass
    # Wait until email CTA is present, then one quick hard refresh (dead first paint).
    _wait_signup_cta_ready(timeout=8, cancel_callback=cancel_callback)
    sleep_with_cancel(0.6, cancel_callback)
    # Early Turnstile/fetch hooks so later profile-step widget is wrapped.
    try:
        _install_turnstile_callback_hook(log_callback=log_callback)
    except Exception:
        pass
    try:
        _reload_signup_page(
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            reason="首次加载后主动硬刷新",
        )
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 主动刷新跳过/失败: {exc}")

    if log_callback:
        try:
            ready = page.run_js("return document.readyState;")
        except Exception:
            ready = "?"
        log_callback(f"[*] 当前URL: {page.url} readyState={ready}")
    click_email_signup_button(
        log_callback=log_callback, cancel_callback=cancel_callback
    )


def has_profile_form(log_callback=None):
    refresh_active_page()
    try:
        return bool(
            page.run_js(
                """
const givenInput = document.querySelector('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"]');
const familyInput = document.querySelector('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"]');
const passwordInput = document.querySelector('input[data-testid="password"], input[name="password"], input[type="password"]');
return !!(givenInput && familyInput && passwordInput);
            """
            )
        )
    except Exception:
        return False


def _email_page_advanced_once(email):
    """检测邮箱提交后页面是否真正前进（离开邮箱输入阶段）。

    点击注册按钮只代表触发了点击，不代表表单真的提交成功。
    若 Cloudflare 挑战未过或页面卡住，按钮点击无实际效果，
    邮箱输入框会一直停留，导致后续空等验证码。

    判定“已前进”的依据：
      - 出现验证码输入框（OTP / code 输入），或
      - 原本可见可用的邮箱输入框已消失/不可用

    返回:
      - True：页面已前进，提交生效
      - False：仍停留在邮箱输入页
    """
    try:
        return bool(
            page.run_js(
                """
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.getAttribute('aria-label'),
        node.getAttribute('placeholder'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
        node.getAttribute('data-testid'),
    ].filter(Boolean).join(' ').replace(/\\s+/g, ' ').trim().toLowerCase();
}
// 1. 出现验证码输入框 => 已前进
const codeInput = Array.from(document.querySelectorAll('input')).find((node) => {
    if (!isVisible(node)) return false;
    const type = (node.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file'].includes(type)) return false;
    const meta = textOf(node);
    const inMode = (node.getAttribute('inputmode') || '').toLowerCase();
    return (
        meta.includes('code') || meta.includes('otp') || meta.includes('verif') ||
        meta.includes('验证') || meta.includes('one-time') || inMode === 'numeric' ||
        node.getAttribute('autocomplete') === 'one-time-code'
    );
});
if (codeInput) return true;
// 2. 邮箱输入框已消失/不可用 => 已前进
const emailInput = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'))
    .find((node) => isVisible(node) && !node.disabled && !node.readOnly);
if (!emailInput) return true;
return false;
                """
            )
        )
    except Exception:
        return False


def _wait_email_page_advanced(email, wait=4.0, cancel_callback=None):
    """点击提交后，在有限窗口内轮询确认页面确实前进。

    给页面/网络一点反应时间：若窗口内检测到已前进则返回 True，
    否则返回 False，由调用方继续重试点击或最终超时换邮箱。
    """
    deadline = time.time() + wait
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if _email_page_advanced_once(email):
            return True
        sleep_with_cancel(0.4, cancel_callback)
    return False


def fill_email_and_submit(timeout=45, log_callback=None, cancel_callback=None):
    raise_if_cancelled(cancel_callback)
    email, dev_token = get_email_and_token()
    if not email or not dev_token:
        raise Exception("获取邮箱失败")
    if log_callback:
        log_callback(f"[*] 已创建邮箱: {email}")
    deadline = time.time() + timeout
    last_diag_time = 0
    last_reclick_time = 0
    last_snapshot = None
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = page.run_js(
            r"""
const email = arguments[0];
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function describeInput(node) {
    return [
        `type=${node.getAttribute('type') || ''}`,
        `name=${node.getAttribute('name') || ''}`,
        `id=${node.getAttribute('id') || ''}`,
        `placeholder=${node.getAttribute('placeholder') || ''}`,
        `aria=${node.getAttribute('aria-label') || ''}`,
        `testid=${node.getAttribute('data-testid') || ''}`,
    ].join(' ').replace(/\s+/g, ' ').trim().slice(0, 160);
}
function describeAction(node) {
    return textOf(node).slice(0, 120);
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const visibleInputs = Array.from(document.querySelectorAll('input, textarea'))
    .filter((node) => isVisible(node) && !node.disabled && !node.readOnly)
    .map(describeInput)
    .slice(0, 8);
const visibleActions = Array.from(document.querySelectorAll('button, a, [role="button"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true')
    .map(describeAction)
    .filter(Boolean)
    .slice(0, 10);
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input) {
    return {
        state: 'not-ready',
        url: location.href,
        title: document.title,
        inputs: visibleInputs,
        buttons: visibleActions,
    };
}
input.focus(); input.click();
const valueProto = input instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
const valueSetter = Object.getOwnPropertyDescriptor(valueProto, 'value')?.set;
const tracker = input._valueTracker;
if (tracker) tracker.setValue('');
if (valueSetter) valueSetter.call(input, email); else input.value = email;
input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new InputEvent('input', { bubbles: true, data: email, inputType: 'insertText' }));
input.dispatchEvent(new Event('change', { bubbles: true }));
const inputType = (input.getAttribute('type') || '').toLowerCase();
const isValid = inputType !== 'email' || input.checkValidity();
if ((input.value || '').trim() !== email || !isValid) {
    return {
        state: 'fill-failed',
        value: input.value || '',
        valid: isValid,
        input: describeInput(input),
        url: location.href,
    };
}
input.blur();
return {
    state: 'filled',
    input: describeInput(input),
    url: location.href,
};
            """,
            email,
        )
        state = filled.get("state") if isinstance(filled, dict) else filled
        if isinstance(filled, dict):
            last_snapshot = filled
        if state == "not-ready":
            now = time.time()
            if now - last_reclick_time >= 3:
                # JS-only click is a no-op on this React button; use native CDP click.
                re_ok, re_detail = _native_click_email_signup(log_callback=log_callback)
                last_reclick_time = now
                if log_callback:
                    log_callback(
                        f"[Debug] 邮箱输入框未出现，已再次触发邮箱注册入口 "
                        f"ok={re_ok} strategy={re_detail}"
                    )
            if log_callback and now - last_diag_time >= 5:
                last_diag_time = now
                inputs = " | ".join((filled or {}).get("inputs", [])[:6]) if isinstance(filled, dict) else ""
                buttons = " | ".join((filled or {}).get("buttons", [])[:8]) if isinstance(filled, dict) else ""
                url = (filled or {}).get("url", page.url if page else "") if isinstance(filled, dict) else (page.url if page else "")
                log_callback(f"[Debug] 等待邮箱输入框: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if state != "filled":
            if log_callback:
                log_callback(f"[Debug] 邮箱输入框已出现，但写入失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue
        sleep_with_cancel(0.8, cancel_callback)
        clicked = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
function textOf(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
        node.getAttribute('placeholder'),
        node.getAttribute('data-testid'),
        node.getAttribute('name'),
        node.getAttribute('id'),
        node.getAttribute('autocomplete'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function emailCandidates() {
    const direct = Array.from(document.querySelectorAll('input[data-testid="email"], input[name="email"], input[type="email"], input[autocomplete="email"], input[placeholder*="mail" i], input[aria-label*="mail" i]'));
    const all = Array.from(document.querySelectorAll('input, textarea'));
    for (const node of all) {
        const type = (node.getAttribute('type') || '').toLowerCase();
        if (['hidden', 'submit', 'button', 'checkbox', 'radio', 'file', 'search'].includes(type)) continue;
        const meta = textOf(node).toLowerCase();
        if (meta.includes('email') || meta.includes('e-mail') || meta.includes('mail') || meta.includes('邮箱') || meta.includes('电子邮件')) {
            direct.push(node);
        }
    }
    return Array.from(new Set(direct));
}
const input = emailCandidates().find((node) => isVisible(node) && !node.disabled && !node.readOnly) || null;
if (!input || !(input.value || '').trim()) return false;
const inputType = (input.getAttribute('type') || '').toLowerCase();
if (inputType === 'email' && !input.checkValidity()) return false;
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]'))
    .filter((node) => isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true');
const submitButton = buttons.find((node) => {
    const text = textOf(node).replace(/\s+/g, '');
    const lower = text.toLowerCase();
    return (
        text === '注册' ||
        text.includes('注册') ||
        text.includes('继续') ||
        text.includes('下一步') ||
        text.includes('确认') ||
        lower.includes('signup') ||
        lower.includes('sign up') ||
        lower.includes('continue') ||
        lower.includes('next') ||
        lower.includes('createaccount') ||
        lower.includes('submit')
    );
});
if (submitButton) {
    submitButton.click();
    return textOf(submitButton) || true;
}
const form = input.closest('form');
if (form) {
    if (form.requestSubmit) form.requestSubmit();
    else form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    return 'form-submit';
}
input.focus();
input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
input.dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', code: 'Enter', bubbles: true, cancelable: true }));
return 'enter';
            """
        )
        if clicked:
            # 点击按钮 != 表单真正提交成功：CF 挑战未过或页面卡住时点击无效果，
            # 邮件不会发出。必须确认页面已离开邮箱输入阶段（邮箱框消失或出现验证码框），
            # 否则继续循环重试点击，最终超时抛异常触发换邮箱重试。
            if _wait_email_page_advanced(email, cancel_callback=cancel_callback):
                if log_callback:
                    detail = f" ({clicked})" if isinstance(clicked, str) else ""
                    log_callback(f"[*] 已填写邮箱并提交: {email}{detail}")
                return email, dev_token
            if log_callback and time.time() - last_diag_time >= 5:
                last_diag_time = time.time()
                log_callback(f"[Debug] 已点击注册但页面未前进，重试提交: {email}")
        sleep_with_cancel(0.5, cancel_callback)
    if last_snapshot:
        inputs = " | ".join(last_snapshot.get("inputs", [])[:6])
        buttons = " | ".join(last_snapshot.get("buttons", [])[:8])
        url = last_snapshot.get("url", page.url if page else "")
        raise Exception(
            f"未找到邮箱输入框或注册按钮，最后页面: url={url}; inputs={inputs or 'none'}; buttons={buttons or 'none'}"
        )
    raise Exception("未找到邮箱输入框或注册按钮")


def fill_code_and_submit(email, dev_token, timeout=180, log_callback=None, cancel_callback=None):
    def _resend_code():
        page.run_js(
            r"""
const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
const target = nodes.find((node) => {
  const t = (node.innerText || node.textContent || '').replace(/\s+/g, '').toLowerCase();
  return t.includes('重新发送') || t.includes('resend') || t.includes('再次发送');
});
if (target && !target.disabled) { target.click(); return true; }
return false;
            """
        )

    code = get_oai_code(
        dev_token,
        email,
        log_callback=log_callback,
        cancel_callback=cancel_callback,
        resend_callback=_resend_code,
    )
    if not code:
        raise Exception("获取验证码失败")
    clean_code = str(code).replace("-", "").strip()
    deadline = time.time() + timeout

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        filled = page.run_js(
            """
const code = String(arguments[0] || '').trim();
if (!code) return 'empty-code';

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function setInputValue(input, value) {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
}

const aggregate = Array.from(document.querySelectorAll(
  'input[data-input-otp=\"true\"], input[name=\"code\"], input[autocomplete=\"one-time-code\"], input[inputmode=\"numeric\"], input[inputmode=\"text\"]'
)).find((node) => isVisible(node) && !node.disabled && !node.readOnly && Number(node.maxLength || 6) > 1);

if (aggregate) {
    aggregate.focus();
    aggregate.click();
    setInputValue(aggregate, code);
    return String(aggregate.value || '').replace(/\\s+/g, '') ? 'filled-aggregate' : 'aggregate-failed';
}

const otpBoxes = Array.from(document.querySelectorAll('input')).filter((node) => {
    if (!isVisible(node) || node.disabled || node.readOnly) return false;
    const maxLength = Number(node.maxLength || 0);
    const ac = String(node.autocomplete || '').toLowerCase();
    return maxLength === 1 || ac === 'one-time-code';
});

if (otpBoxes.length >= code.length) {
    for (let i = 0; i < code.length; i += 1) {
        const ch = code[i] || '';
        const box = otpBoxes[i];
        box.focus();
        box.click();
        setInputValue(box, ch);
        box.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: ch }));
        box.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true, key: ch }));
    }
    const merged = otpBoxes.slice(0, code.length).map((x) => String(x.value || '').trim()).join('');
    return merged.length ? 'filled-boxes' : 'boxes-failed';
}

return 'not-ready';
            """,
            clean_code,
        )

        if filled == "not-ready":
            sleep_with_cancel(0.5, cancel_callback)
            continue
        if "failed" in str(filled):
            if log_callback:
                log_callback(f"[Debug] 验证码填写失败: {filled}")
            sleep_with_cancel(0.5, cancel_callback)
            continue

        clicked = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

const buttons = Array.from(document.querySelectorAll('button[type=\"submit\"], button')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});

const btn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    return (
        t.includes('确认邮箱') ||
        t.includes('继续') ||
        t.includes('下一步') ||
        t.includes('confirm') ||
        t.includes('continue') ||
        t.includes('next')
    );
});

if (!btn) return 'no-button';
btn.focus();
btn.click();
return 'clicked';
            """
        )

        if clicked == "clicked" or clicked == "no-button":
            if log_callback:
                log_callback(f"[*] 已填写验证码并提交: {code}")
            # Give SPA time to advance; Browser Use remote tabs can flicker after OTP.
            sleep_with_cancel(2.0, cancel_callback)
            if not _page_is_alive():
                raise AccountRetryNeeded("验证码提交后远端页面/会话已断开，重试账号")
            # Wait briefly for profile form to appear (best-effort)
            wait_end = time.time() + 12
            while time.time() < wait_end:
                raise_if_cancelled(cancel_callback)
                if not _page_is_alive():
                    raise AccountRetryNeeded("验证码提交后远端页面/会话已断开，重试账号")
                if has_profile_form(log_callback=None):
                    break
                sleep_with_cancel(0.5, cancel_callback)
            return code

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("验证码已获取，但自动填写/提交失败")


# Shared JS: detect Turnstile even when the challenge iframe lives in a *closed*
# shadow root (accounts.x.ai). Light DOM only exposes:
#   <div style="height:64px"><div><input name=cf-turnstile-response id=cf-chl-widget-XXX_response></div></div>
# The real iframe (id=cf-chl-widget-XXX, title="Widget … Cloudflare") is piercable
# only via CDP / a11y — so click coords come from the sized host wrapping the
# hidden response field. Localized UI text (e.g. "Sahkan anda manusia") is NOT
# in document.body.innerText.
_TURNSTILE_DETECT_JS = r"""
function isVisible(node) {
  if (!node) return false;
  const s = getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
function humanCheckText(t) {
  t = String(t || '').toLowerCase();
  return (
    t.includes('verify you are human') ||
    t.includes('confirm you are human') ||
    t.includes('i am human') ||
    t.includes('are you human') ||
    t.includes('sahkan anda manusia') ||
    t.includes('saya manusia') ||
    t.includes('我是真人') ||
    t.includes('确认您是真人') ||
    t.includes('请确认您是真人') ||
    t.includes('人間であることを確認') ||
    t.includes('私は人間') ||
    t.includes('cloudflare')
  );
}
function sizedHostOf(el) {
  // Walk up from hidden cf-turnstile-response to the compact host Turnstile sizes
  // (typically ~300-400 x 55-75, sitting just above Complete sign up).
  let n = el ? el.parentElement : null;
  let best = null;
  for (let i = 0; i < 8 && n; i++) {
    const r = n.getBoundingClientRect();
    if (r.width >= 200 && r.width <= 520 && r.height >= 40 && r.height <= 100) {
      best = n;
      break;
    }
    // Prefer the tightest reasonably-tall wrapper near the form width
    if (r.width >= 200 && r.height >= 50 && r.height <= 140 && !best) best = n;
    n = n.parentElement;
  }
  return best;
}
function checkboxPoint(node, source) {
  if (!node) return null;
  try { node.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) {}
  const r = node.getBoundingClientRect();
  if (!(r.width > 0 && r.height > 0)) return null;
  // Checkbox is the left square inside the widget bar — not the Cloudflare logo.
  return {
    x: r.left + Math.min(30, Math.max(14, r.width * 0.07)),
    y: r.top + r.height / 2,
    w: r.width,
    h: r.height,
    tag: node.tagName,
    source: source || node.tagName,
  };
}
const cfInput = document.querySelector(
  'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"], input[name*="turnstile" i], input[id*="cf-chl-widget"]'
);
// Primary host on accounts.x.ai: sized parent of the hidden response field.
const responseHost = sizedHostOf(cfInput);
const hosts = Array.from(document.querySelectorAll(
  '.cf-turnstile, [data-sitekey], iframe[src*="turnstile"], iframe[src*="challenges.cloudflare"], iframe[src*="cf-chl"], iframe[id*="cf-chl-widget"]'
)).filter(isVisible);
// Managed Turnstile often injects blank-src / closed-shadow iframes; also match
// compact iframes near Complete.
const compactIframes = Array.from(document.querySelectorAll('iframe')).filter((f) => {
  if (!isVisible(f)) return false;
  const r = f.getBoundingClientRect();
  const src = String(f.src || '');
  const id = String(f.id || '');
  if (/turnstile|challenges\.cloudflare|cf-chl|cloudflare/i.test(src + ' ' + id)) return true;
  return r.width >= 200 && r.width <= 420 && r.height >= 40 && r.height <= 90;
});
const textHosts = Array.from(document.querySelectorAll('div, label, span, p, section, form'))
  .filter(isVisible)
  .filter((n) => {
    const t = (n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t || t.length > 120) return false;
    return humanCheckText(t);
  })
  .slice(0, 6);
// Complete button — used as geometric fallback (widget sits ~65-90px above it).
const completeBtn = Array.from(document.querySelectorAll('button')).find((b) =>
  /complete\s*sign\s*up|完成注册|完成註冊/i.test((b.innerText || b.textContent || '').trim())
);
let clickTarget = null;
let pointSource = '';
const checkboxish = Array.from(document.querySelectorAll(
  'input[type="checkbox"], [role="checkbox"], label'
)).filter(isVisible).find((n) => {
  const t = ((n.innerText || n.textContent || '') + ' ' + (n.getAttribute('aria-label') || '')).toLowerCase();
  return humanCheckText(t) || /human|manusia|真人|人間|cloudflare|turnstile/.test(t);
});
if (checkboxish) { clickTarget = checkboxish; pointSource = 'checkboxish'; }
if (!clickTarget && hosts[0]) { clickTarget = hosts[0]; pointSource = 'host'; }
if (!clickTarget && compactIframes[0]) { clickTarget = compactIframes[0]; pointSource = 'compactIframe'; }
if (!clickTarget && responseHost) { clickTarget = responseHost; pointSource = 'responseHost'; }
if (!clickTarget && textHosts[0]) {
  let n = textHosts[0];
  for (let i = 0; i < 5 && n; i++) {
    const r = n.getBoundingClientRect();
    if (r.width >= 180 && r.height >= 40 && r.height <= 120) { clickTarget = n; break; }
    n = n.parentElement;
  }
  if (!clickTarget) clickTarget = textHosts[0];
  pointSource = 'textHost';
}
let point = checkboxPoint(clickTarget, pointSource);
// Geometric fallback: left of the bar just above Complete (matches live layout).
if (!point && completeBtn && cfInput) {
  const br = completeBtn.getBoundingClientRect();
  if (br.width > 0) {
    point = {
      x: br.left + 28,
      y: br.top - 40,
      w: br.width,
      h: 65,
      tag: 'GEOM',
      source: 'aboveComplete',
    };
  }
}
const token = String((cfInput && cfInput.value) || '').trim();
let apiToken = '';
try {
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    apiToken = String(turnstile.getResponse() || '').trim();
  }
} catch (e) {}
const bodyText = (document.body && document.body.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 500);
const visibleHuman = humanCheckText(bodyText) || /sahkan anda manusia|verify you are human|cloudflare/i.test(bodyText);
const hostRect = responseHost ? (() => {
  const r = responseHost.getBoundingClientRect();
  return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
})() : null;
const completeRect = completeBtn ? (() => {
  const r = completeBtn.getBoundingClientRect();
  return { x: Math.round(r.left), y: Math.round(r.top), w: Math.round(r.width), h: Math.round(r.height) };
})() : null;
return {
  hasCfInput: !!cfInput,
  cfInputId: cfInput ? String(cfInput.id || '') : '',
  tokenLen: token.length,
  apiTokenLen: apiToken.length,
  solved: token.length >= 80 || apiToken.length >= 80,
  // responseHost alone is a real widget signal on accounts.x.ai
  widgets: hosts.length + compactIframes.length + (responseHost ? 1 : 0),
  hostCount: hosts.length,
  compactIframes: compactIframes.length,
  hasResponseHost: !!responseHost,
  responseHost: hostRect,
  complete: completeRect,
  textHits: textHosts.map((n) => (n.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 50)).slice(0, 4),
  visibleHuman,
  point,
  iframes: Array.from(document.querySelectorAll('iframe')).slice(0, 8).map((f) => {
    const r = f.getBoundingClientRect();
    return { src: String(f.src || '').slice(0, 100), id: String(f.id || ''), w: Math.round(r.width), h: Math.round(r.height), vis: isVisible(f) };
  }),
};
"""


def _read_turnstile_token():
    """Return current Turnstile response token from page, or empty string."""
    global page
    if page is None:
        return ""
    try:
        token = page.run_js(
            """
try {
  const byInput = String((document.querySelector('input[name="cf-turnstile-response"]') || {}).value || '').trim();
  if (byInput) return byInput;
  const alts = Array.from(document.querySelectorAll(
    'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"], input[name*="turnstile" i]'
  ));
  for (const el of alts) {
    const v = String(el.value || '').trim();
    if (v.length >= 80) return v;
  }
  if (window.turnstile && typeof turnstile.getResponse === 'function') {
    return String(turnstile.getResponse() || '').trim();
  }
  return '';
} catch(e) { return ''; }
            """
        )
        return str(token or "").strip()
    except Exception:
        return ""


def _probe_turnstile_state():
    """Lightweight diagnostics for CF wait loops (includes localized widgets)."""
    global page
    if page is None:
        return {}
    try:
        state = page.run_js(_TURNSTILE_DETECT_JS) or {}
        if isinstance(state, dict):
            state["title"] = page.run_js("return document.title;") if page else ""
            state["url"] = page.run_js("return location.href;") if page else ""
        return state
    except Exception as exc:
        return {"error": str(exc)[:160]}


def _turnstile_needs_solve():
    """
    True when a human-check is present/likely and token not ready.

    Important: empty input[name=cf-turnstile-response] is enough signal on
    accounts.x.ai — the checkbox often paints late / in a blank-src iframe that
    our widget counters miss. Never treat empty CF field as "no challenge".
    """
    state = _probe_turnstile_state()
    if not isinstance(state, dict):
        return False
    if state.get("solved"):
        return False
    if int(state.get("tokenLen") or 0) >= 80:
        return False
    if int(state.get("apiTokenLen") or 0) >= 80:
        return False
    if int(state.get("widgets") or 0) > 0:
        return True
    if state.get("visibleHuman") or state.get("textHits") or state.get("point"):
        return True
    # Empty CF field = challenge pending (this was the bug: we skipped and Complete-rushed)
    if state.get("hasCfInput") and int(state.get("tokenLen") or 0) < 80:
        return True
    return False


def _turnstile_click_point():
    """Client coordinates of Turnstile checkbox (left side of widget)."""
    state = _probe_turnstile_state()
    if not isinstance(state, dict):
        return None
    point = state.get("point")
    if isinstance(point, dict) and point.get("x") is not None:
        return point
    return None


# Last CapMonster/local token for re-inject if SPA clears the hidden field.
_LAST_TURNSTILE_TOKEN = ""


# Injected on every document (Page.addScriptToEvaluateOnNewDocument) so we
# capture turnstile.render callbacks even if the SPA loads the API later.
_TURNSTILE_BOOT_HOOK_JS = r"""
(function () {
  if (window.__grokTsBootHook) return;
  window.__grokTsBootHook = true;
  window.__grokTsCallbacks = window.__grokTsCallbacks || [];
  window.__grokTsWidgetIds = window.__grokTsWidgetIds || [];
  window.__grokTurnstileToken = window.__grokTurnstileToken || '';
  window.__grokNetLog = window.__grokNetLog || [];
  function wrapTurnstile(ts) {
    if (!ts || ts.__grokWrapped) return ts;
    try {
      const origRender = typeof ts.render === 'function' ? ts.render.bind(ts) : null;
      if (origRender) {
        ts.render = function (container, params) {
          try {
            const p = params || {};
            if (typeof p.callback === 'function') window.__grokTsCallbacks.push(p.callback);
            if (typeof p['success-callback'] === 'function') window.__grokTsCallbacks.push(p['success-callback']);
          } catch (e) {}
          const id = origRender(container, params);
          try { if (id != null) window.__grokTsWidgetIds.push(id); } catch (e) {}
          return id;
        };
      }
      const origGet = typeof ts.getResponse === 'function' ? ts.getResponse.bind(ts) : null;
      ts.getResponse = function (widgetId) {
        try {
          if (origGet) {
            const v = String(origGet(widgetId) || '').trim();
            if (v.length >= 50) return v;
          }
        } catch (e) {}
        return String(window.__grokTurnstileToken || '');
      };
      try { ts.isExpired = function () { return false; }; } catch (e) {}
      ts.__grokWrapped = true;
    } catch (e) {}
    return ts;
  }
  // Soft wrap only — never redefine window.turnstile (breaks some CF/SPA loads).
  function tryWrap() {
    try { if (window.turnstile) wrapTurnstile(window.turnstile); } catch (e) {}
  }
  tryWrap();
  try {
    const id = setInterval(function () {
      tryWrap();
      if (window.turnstile && window.turnstile.__grokWrapped) clearInterval(id);
    }, 200);
    setTimeout(function () { try { clearInterval(id); } catch (e) {} }, 60000);
  } catch (e) {}
  try {
    const ofetch = window.fetch;
    if (typeof ofetch === 'function' && !window.__grokFetchHooked) {
      window.fetch = function () {
        try {
          const a0 = arguments[0];
          const u = String(typeof a0 === 'string' ? a0 : (a0 && a0.url) || '');
          if (/sign|auth|account|register|signup|session|sso|x\.ai|turnstile/i.test(u)) {
            window.__grokNetLog.push({ t: Date.now(), kind: 'fetch', url: u.slice(0, 180) });
            if (window.__grokNetLog.length > 40) window.__grokNetLog.shift();
          }
        } catch (e) {}
        return ofetch.apply(this, arguments);
      };
      window.__grokFetchHooked = true;
    }
  } catch (e) {}
})();
"""


def _install_turnstile_callback_hook(log_callback=None):
    """Intercept turnstile.render so we can later fire the SPA success callback.

    accounts.x.ai (React) typically only marks the form valid inside the
    ``callback`` passed to ``turnstile.render``. Filling the hidden input alone
    does not flip that React state — Complete then no-ops.
    """
    global page
    if page is None:
        return False
    # Persist across SPA navigations / reloads inside the same browser profile.
    try:
        page.run_cdp(
            "Page.addScriptToEvaluateOnNewDocument",
            source=_TURNSTILE_BOOT_HOOK_JS,
        )
    except Exception:
        pass
    try:
        ok = page.run_js(_TURNSTILE_BOOT_HOOK_JS + "\ntrue;")
        # Ensure in-page runtime flag used by older checks
        page.run_js("window.__grokTsHookInstalled = true; true;")
        if log_callback and ok is not False:
            log_callback("[Debug] Turnstile render/callback hook 已安装")
        return True
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] Turnstile hook 安装失败: {exc}")
        return False


def _fire_turnstile_success_callbacks(token, log_callback=None):
    """Invoke captured render callbacks + React onChange / common globals with token."""
    global page
    if page is None or not token:
        return 0
    try:
        n = page.run_js(
            """
const token = String(arguments[0] || '').trim();
if (!token) return 0;
window.__grokTurnstileToken = token;
let fired = 0;
const cbs = window.__grokTsCallbacks || [];
for (const cb of cbs) {
  try { cb(token); fired += 1; } catch (e) {}
}
// data-callback attribute names
const hosts = Array.from(document.querySelectorAll('[data-callback]'));
for (const host of hosts) {
  const name = host.getAttribute('data-callback');
  if (name && typeof window[name] === 'function') {
    try { window[name](token); fired += 1; } catch (e) {}
  }
}
// Widget ids: some SPAs poll getResponse(widgetId)
try {
  if (window.turnstile) {
    turnstile.getResponse = function() { return token; };
    turnstile.isExpired = function() { return false; };
  }
} catch (e) {}

// React 17/18: walk fiber from cf-turnstile-response and fire onChange/onInput
function reactKeys(el) {
  return el ? Object.keys(el).filter((k) =>
    k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$') ||
    k.startsWith('__reactProps$') || k.startsWith('__reactEventHandlers$')
  ) : [];
}
function callMaybe(fn, arg) {
  if (typeof fn === 'function') {
    try { fn(arg); return true; } catch (e) { return false; }
  }
  return false;
}
const cfInputs = Array.from(document.querySelectorAll(
  'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"], input[id*="cf-chl-widget"]'
));
for (const el of cfInputs) {
  for (const k of reactKeys(el)) {
    try {
      const val = el[k];
      // Props bag
      if (val && (val.onChange || val.onInput || val.onBlur)) {
        const fakeEv = { target: el, currentTarget: el, type: 'change', bubbles: true, preventDefault(){}, stopPropagation(){} };
        if (callMaybe(val.onChange, fakeEv)) fired += 1;
        if (callMaybe(val.onInput, fakeEv)) fired += 1;
      }
      // Fiber node — walk up a few parents for form handlers
      let fiber = val;
      for (let i = 0; i < 12 && fiber; i++) {
        const props = fiber.memoizedProps || fiber.pendingProps || {};
        if (callMaybe(props.onChange, { target: el, currentTarget: el })) fired += 1;
        if (callMaybe(props.onSuccess, token)) fired += 1;
        if (callMaybe(props.callback, token)) fired += 1;
        if (callMaybe(props.onVerify, token)) fired += 1;
        // Also look for captchaToken setters in state-like props
        if (typeof props.setCaptchaToken === 'function') {
          try { props.setCaptchaToken(token); fired += 1; } catch (e) {}
        }
        fiber = fiber.return;
      }
    } catch (e) {}
  }
}
// Enable Complete button if still disabled after "solve"
try {
  Array.from(document.querySelectorAll('button, [role="button"]')).forEach((btn) => {
    const t = ((btn.innerText || btn.textContent || '') + '').replace(/\\s+/g, ' ').trim().toLowerCase();
    if (!t.includes('complete') && !t.includes('完成注册') && !t.includes('create account')) return;
    try { btn.disabled = false; btn.removeAttribute('disabled'); btn.setAttribute('aria-disabled', 'false'); } catch (e) {}
  });
} catch (e) {}
return fired;
            """,
            token,
        )
        n = int(n or 0)
        if log_callback:
            log_callback(f"[*] 已触发 Turnstile success callback 次数={n}")
        return n
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] 触发 Turnstile callback 失败: {exc}")
        return 0


def _diagnose_complete_button(log_callback=None):
    """Log whether Complete is disabled and if signup network fired recently."""
    global page
    if page is None:
        return {}
    try:
        info = page.run_js(
            r"""
function isVisible(n) {
  if (!n) return false;
  const s = getComputedStyle(n);
  if (s.display==='none'||s.visibility==='hidden'||s.opacity==='0') return false;
  const r = n.getBoundingClientRect();
  return r.width>0 && r.height>0;
}
const buttons = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]'))
  .filter(isVisible)
  .map((b) => {
    const t = (b.innerText || b.textContent || b.value || '').replace(/\s+/g,' ').trim();
    return {
      text: t.slice(0, 40),
      disabled: !!b.disabled,
      ariaDisabled: b.getAttribute('aria-disabled'),
      type: b.getAttribute('type') || b.tagName,
      cls: String(b.className || '').slice(0, 60),
    };
  })
  .filter((x) => /complete|sign up|create|提交|注册|登録/i.test(x.text));
const cf = document.querySelector('input[name="cf-turnstile-response"]');
const net = (window.__grokNetLog || []).slice(-8);
return {
  buttons,
  tokenLen: String((cf && cf.value) || '').trim().length,
  cbCount: (window.__grokTsCallbacks || []).length,
  hasTurnstile: !!window.turnstile,
  turnstileWrapped: !!(window.turnstile && window.turnstile.__grokWrapped),
  net,
};
            """
        )
        if log_callback and info:
            log_callback(f"[Debug] Complete 按钮诊断: {info}")
        return info or {}
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] Complete 诊断失败: {exc}")
        return {}


def _inject_turnstile_token(token, log_callback=None):
    """Write CapMonster/local token into cf-turnstile-response + stub getResponse.

    accounts.x.ai is a React SPA: filling the hidden input alone often is not enough.
    We also stub ``turnstile.getResponse``, fire ``data-callback`` / known globals,
    and try to invoke render-time success callbacks stored on the widget host.
    """
    global page, _LAST_TURNSTILE_TOKEN
    if page is None or not token:
        return 0
    token = str(token).strip()
    if not token:
        return 0
    _LAST_TURNSTILE_TOKEN = token
    try:
        synced = page.run_js(
            """
const token = String(arguments[0] || '').trim();
if (!token) return 0;
window.__grokTurnstileToken = token;

const inputs = Array.from(document.querySelectorAll(
  'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"], input[name*="turnstile" i], input[id*="cf-chl-widget"]'
));
// Ensure at least one field exists for form POST bodies.
if (!inputs.length) {
  try {
    const holder = document.querySelector('form') || document.body;
    const el = document.createElement('input');
    el.type = 'hidden';
    el.name = 'cf-turnstile-response';
    el.id = 'cf-chl-widget-injected_response';
    holder.appendChild(el);
    inputs.push(el);
  } catch (e) {}
}
let best = 0;
for (const cfInput of inputs) {
  try {
    const proto = cfInput.tagName === 'TEXTAREA'
      ? window.HTMLTextAreaElement.prototype
      : window.HTMLInputElement.prototype;
    const nativeSetter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
    if (nativeSetter) nativeSetter.call(cfInput, token);
    else cfInput.value = token;
  } catch (e) {
    try { cfInput.value = token; } catch (e2) {}
  }
  try { cfInput.setAttribute('value', token); } catch (e) {}
  try {
    cfInput.dispatchEvent(new InputEvent('input', { bubbles: true, data: token, inputType: 'insertText' }));
  } catch (e) {
    cfInput.dispatchEvent(new Event('input', { bubbles: true }));
  }
  cfInput.dispatchEvent(new Event('change', { bubbles: true }));
  best = Math.max(best, String(cfInput.value || '').trim().length);
}

// Stub Turnstile JS API so SPA handlers that call getResponse() see the token.
try {
  if (!window.turnstile) window.turnstile = {};
  const fixed = token;
  const origGet = typeof turnstile.getResponse === 'function' ? turnstile.getResponse.bind(turnstile) : null;
  turnstile.getResponse = function (widgetId) {
    try {
      if (origGet) {
        const v = String(origGet(widgetId) || '').trim();
        if (v.length >= 50) return v;
      }
    } catch (e) {}
    return fixed;
  };
  turnstile.isExpired = function () { return false; };
  try {
    turnstile.reset = function () { return undefined; };
  } catch (e) {}
} catch (e) {}

// Fire every data-callback / onSuccess-style hook we can find on hosts.
const hosts = Array.from(document.querySelectorAll(
  '[data-callback], .cf-turnstile, [class*="cf-turnstile"], [id^="cf-chl-widget"], [data-sitekey]'
));
const cbNames = new Set();
for (const host of hosts) {
  try {
    const cbName = host.getAttribute && host.getAttribute('data-callback');
    if (cbName) cbNames.add(cbName);
  } catch (e) {}
  // Some apps stash props on the node
  try {
    if (typeof host._cf_callback === 'function') {
      try { host._cf_callback(token); } catch (e) {}
    }
  } catch (e) {}
}
for (const name of cbNames) {
  try {
    if (typeof window[name] === 'function') window[name](token);
  } catch (e) {}
}
// Common global names used by wrappers
for (const name of ['onTurnstileSuccess', 'onCaptchaSuccess', 'cfCallback', 'turnstileCallback']) {
  try {
    if (typeof window[name] === 'function') window[name](token);
  } catch (e) {}
}

// Notify listeners that may watch storage / custom events
try {
  window.dispatchEvent(new CustomEvent('turnstile-success', { detail: { token } }));
  document.dispatchEvent(new CustomEvent('cf-turnstile-response', { detail: { token }, bubbles: true }));
} catch (e) {}

// Managed Turnstile posts completion via window.message from the widget iframe.
// Simulate several known shapes so SPA message handlers flip "solved" state.
try {
  const origins = [
    'https://challenges.cloudflare.com',
    'https://challenges.cloudflare.com/',
    location.origin,
  ];
  const payloads = [
    { event: 'complete', token },
    { source: 'cloudflare-challenge', event: 'complete', token },
    { event: 'error', error: null, token },
    { code: 'complete', token },
    { type: 'complete', token },
    ['complete', token],
  ];
  for (const origin of origins) {
    for (const data of payloads) {
      try {
        window.dispatchEvent(new MessageEvent('message', {
          data,
          origin,
          source: window,
        }));
      } catch (e) {}
    }
  }
} catch (e) {}

return best;
            """,
            token,
        )
        n = int(synced or 0)
        if log_callback and n:
            log_callback(f"[*] Turnstile token 已回填，长度={n}")
        elif log_callback and not n:
            log_callback("[!] Turnstile token 回填后字段仍为空（DOM 可能未挂载）")
        # Critical: notify React via the original turnstile.render callback.
        _fire_turnstile_success_callbacks(token, log_callback=log_callback)
        return n
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] Turnstile token 回填失败: {exc}")
        return 0


def _ensure_turnstile_token_present(log_callback=None):
    """Re-inject last CapMonster token if SPA/CF cleared the hidden field."""
    global _LAST_TURNSTILE_TOKEN
    cur = _read_turnstile_token()
    if len(cur) >= 50:
        return cur
    if _LAST_TURNSTILE_TOKEN and len(_LAST_TURNSTILE_TOKEN) >= 50:
        if log_callback:
            log_callback(
                f"[*] 隐藏字段 token 已丢失，重新回填 CapMonster token（长度={len(_LAST_TURNSTILE_TOKEN)}）"
            )
        _inject_turnstile_token(_LAST_TURNSTILE_TOKEN, log_callback=log_callback)
        return _read_turnstile_token() or _LAST_TURNSTILE_TOKEN
    return cur


def _form_request_submit_complete(log_callback=None):
    """Native form.requestSubmit + React fiber onClick/onSubmit after token inject."""
    global page
    if page is None:
        return "no-page"
    try:
        result = page.run_js(
            r"""
const token = String(window.__grokTurnstileToken || '').trim();
const cf = document.querySelector(
  'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"], input[id*="cf-chl-widget"]'
);
if (cf && token && String(cf.value || '').trim().length < 50) {
  try {
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    if (nativeSetter) nativeSetter.call(cf, token);
    else cf.value = token;
  } catch (e) { cf.value = token; }
}
function isVisible(node) {
  if (!node) return false;
  const s = getComputedStyle(node);
  if (s.display === 'none' || s.visibility === 'hidden' || s.opacity === '0') return false;
  const r = node.getBoundingClientRect();
  return r.width > 0 && r.height > 0;
}
function buttonText(node) {
  return [node.innerText, node.textContent, node.getAttribute('value'), node.getAttribute('aria-label')]
    .filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function reactPropKeys(el) {
  return el ? Object.keys(el).filter((k) =>
    k.startsWith('__reactProps$') || k.startsWith('__reactEventHandlers$') ||
    k.startsWith('__reactFiber$') || k.startsWith('__reactInternalInstance$')
  ) : [];
}
function fireReactHandlers(el, kind) {
  let n = 0;
  // Walk element + parents (Radix/shadcn often put handlers on a wrapper)
  let node = el;
  for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
    for (const k of reactPropKeys(node)) {
      try {
        const bag = node[k];
        if (!bag) continue;
        if (typeof bag.onClick === 'function' && kind === 'click') {
          try {
            bag.onClick({
              target: el, currentTarget: node, type: 'click', bubbles: true,
              preventDefault(){}, stopPropagation(){}, nativeEvent: { isTrusted: true },
              isTrusted: true,
            });
            n += 1;
          } catch (e) {}
        }
        if (typeof bag.onSubmit === 'function' && kind === 'submit') {
          try {
            bag.onSubmit({
              target: el, currentTarget: node, type: 'submit', bubbles: true,
              preventDefault(){}, stopPropagation(){},
            });
            n += 1;
          } catch (e) {}
        }
        let fiber = bag;
        for (let i = 0; i < 20 && fiber; i++) {
          const props = fiber.memoizedProps || fiber.pendingProps || {};
          if (kind === 'click' && typeof props.onClick === 'function') {
            try {
              props.onClick({
                target: el, currentTarget: node, type: 'click', bubbles: true,
                preventDefault(){}, stopPropagation(){}, isTrusted: true,
              });
              n += 1;
            } catch (e) {}
          }
          if (kind === 'submit' && typeof props.onSubmit === 'function') {
            try {
              props.onSubmit({
                target: el, currentTarget: node, type: 'submit', bubbles: true,
                preventDefault(){}, stopPropagation(){},
              });
              n += 1;
            } catch (e) {}
          }
          if (token) {
            for (const key of Object.keys(props || {})) {
              if (/captcha|turnstile|cfToken|challenge|setToken/i.test(key) && typeof props[key] === 'function') {
                try { props[key](token); n += 1; } catch (e) {}
              }
            }
          }
          fiber = fiber.return;
        }
      } catch (e) {}
    }
  }
  return n;
}
const btn = document.querySelector('[data-grok-complete-signup="1"]') || Array.from(
  document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')
).find((n) => {
  if (!isVisible(n) || n.disabled) return false;
  const t = buttonText(n).replace(/\s+/g, '').toLowerCase();
  if (t.includes('email') && !t.includes('complete')) return false;
  if (t.includes('goback') || t.includes('返回')) return false;
  return t.includes('completesignup') || t.includes('completeyoursignup') ||
    t.includes('createaccount') || t.includes('完成注册') || t.includes('创建账户') ||
    (t.includes('sign up') && !t.includes('with'));
});
const parts = [];
if (btn) {
  try { btn.removeAttribute('disabled'); btn.disabled = false; } catch (e) {}
  try { btn.setAttribute('aria-disabled', 'false'); } catch (e) {}
  const form = btn.closest('form') || document.querySelector('form');
  // Push captcha into React state from form/cf fibers first
  if (cf) parts.push('reactCf=' + fireReactHandlers(cf, 'change'));
  if (form) parts.push('reactForm=' + fireReactHandlers(form, 'submit'));
  parts.push('reactBtn=' + fireReactHandlers(btn, 'click'));
  if (form && typeof form.requestSubmit === 'function') {
    try { form.requestSubmit(btn); parts.push('requestSubmit'); } catch (e) {}
  }
  try { btn.click(); parts.push('btn.click'); } catch (e) {}
}
return parts.length ? parts.join('|') : 'no-submit';
            """
        )
        if log_callback:
            log_callback(f"[Debug] form submit fallback: {result}")
        return str(result or "unknown")
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] form submit fallback failed: {exc}")
        return f"err:{exc}"


# ---------------------------------------------------------------------------
# CapMonster Cloud — TurnstileTaskProxyless
# Docs: https://docs.capmonster.cloud/docs/captchas/turnstile-task
# API:  https://api.capmonster.cloud  createTask / getTaskResult
# ---------------------------------------------------------------------------

def get_capmonster_api_key():
    key = str(config.get("capmonster_api_key") or "").strip()
    if not key:
        key = os.environ.get("CAPMONSTER_API_KEY", "").strip()
    return key


def capmonster_is_enabled():
    if config.get("capmonster_enabled") is False:
        return False
    return bool(get_capmonster_api_key())


def _capmonster_api_base():
    base = str(config.get("capmonster_api_base") or "https://api.capmonster.cloud").strip()
    return base.rstrip("/") or "https://api.capmonster.cloud"


def _extract_turnstile_sitekey(log_callback=None):
    """
    Pull websiteKey from the live page.

    accounts.x.ai puts the real iframe in a closed shadow root; sitekey is in the
    iframe URL path: .../turnstile/.../0x4AAAAA.../light/...
    """
    global page
    if page is None:
        return ""

    # 1) Light DOM + open attributes
    try:
        from_dom = page.run_js(
            r"""
const keys = new Set();
for (const n of document.querySelectorAll('[data-sitekey]')) {
  const k = (n.getAttribute('data-sitekey') || '').trim();
  if (k) keys.add(k);
}
for (const f of document.querySelectorAll('iframe')) {
  const src = f.src || f.getAttribute('src') || '';
  const m = src.match(/(0x[0-9A-Za-z_-]{10,})/);
  if (m) keys.add(m[1]);
}
// scripts / HTML dump
const html = document.documentElement.innerHTML || '';
for (const m of html.matchAll(/data-sitekey=["']([^"']+)["']/gi)) keys.add(m[1]);
for (const m of html.matchAll(/(0x4[0-9A-Za-z_-]{10,})/g)) keys.add(m[1]);
return Array.from(keys);
            """
        )
        if isinstance(from_dom, list):
            for k in from_dom:
                k = str(k or "").strip()
                if k.startswith("0x") and len(k) >= 20:
                    if log_callback:
                        log_callback(f"[*] Turnstile sitekey (DOM): {k[:24]}…")
                    return k
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] sitekey DOM 提取失败: {exc}")

    # 2) CDP pierce closed shadow → iframe src
    try:
        doc = page.run_cdp("DOM.getDocument", depth=-1, pierce=True)
        root = doc.get("root") if isinstance(doc, dict) else None
        found = []

        def walk(node):
            if not node or len(found) > 5:
                return
            name = str(node.get("nodeName") or "").upper()
            attrs = node.get("attributes") or []
            amap = {}
            for i in range(0, len(attrs) - 1, 2):
                amap[str(attrs[i])] = str(attrs[i + 1])
            if name == "IFRAME":
                src = amap.get("src", "") or ""
                m = re.search(r"(0x[0-9A-Za-z_-]{10,})", src)
                if m:
                    found.append(m.group(1))
            for c in node.get("children") or []:
                walk(c)
            for s in node.get("shadowRoots") or []:
                walk(s)
            if node.get("contentDocument"):
                walk(node["contentDocument"])

        if root:
            walk(root)
        if found:
            if log_callback:
                log_callback(f"[*] Turnstile sitekey (CDP): {found[0][:24]}…")
            return found[0]
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] sitekey CDP 提取失败: {exc}")

    # 3) Config override
    override = str(config.get("capmonster_website_key") or "").strip()
    if override:
        if log_callback:
            log_callback(f"[*] Turnstile sitekey (config): {override[:24]}…")
        return override
    return ""


def _turnstile_website_url():
    global page
    try:
        if page is not None:
            href = page.run_js("return location.href;")
            if href and str(href).startswith("http"):
                return str(href)
    except Exception:
        pass
    return "https://accounts.x.ai/sign-up?redirect=grok-com"


def solve_turnstile_capmonster(
    website_url=None,
    website_key=None,
    log_callback=None,
    cancel_callback=None,
    timeout=None,
):
    """
    Create TurnstileTaskProxyless on CapMonster Cloud and poll until token.

    Returns token string (usually 300–2000+ chars) or raises Exception.
    """
    api_key = get_capmonster_api_key()
    if not api_key:
        raise Exception("capmonster_api_key 未配置（config.json / CAPMONSTER_API_KEY）")

    website_url = (website_url or _turnstile_website_url() or "").strip()
    website_key = (website_key or _extract_turnstile_sitekey(log_callback=log_callback) or "").strip()
    if not website_key:
        raise Exception(
            "无法提取 Turnstile sitekey（页面 iframe / data-sitekey）。"
            "可在 config 设置 capmonster_website_key"
        )
    if not website_url:
        raise Exception("websiteURL 为空")

    timeout_s = int(timeout or config.get("capmonster_timeout") or 120)
    timeout_s = max(timeout_s, 30)
    poll = float(config.get("capmonster_poll_interval") or 2.0)
    poll = max(poll, 1.0)
    base = _capmonster_api_base()

    # Prefer proxyless; optional proxy task if config.proxy set and capmonster_use_proxy
    task = {
        "type": "TurnstileTaskProxyless",
        "websiteURL": website_url,
        "websiteKey": website_key,
    }
    # Optional cloudflare fields if present on page (helps some managed widgets)
    try:
        extra = page.run_js(
            r"""
const out = {};
try {
  const p = window.__CF$cv$params || {};
  if (p.r) out.cfRay = String(p.r);
} catch(e) {}
const actionEl = document.querySelector('[data-action]');
if (actionEl) out.pageAction = actionEl.getAttribute('data-action') || '';
return out;
            """
        ) if page is not None else {}
        if isinstance(extra, dict):
            if extra.get("pageAction"):
                task["pageAction"] = str(extra["pageAction"])
    except Exception:
        pass

    if log_callback:
        log_callback(
            f"[*] CapMonster Cloud: createTask TurnstileTaskProxyless "
            f"key={website_key[:18]}… url={website_url[:60]}"
        )

    create_payload = {"clientKey": api_key, "task": task}
    try:
        resp = requests.post(
            f"{base}/createTask",
            json=create_payload,
            timeout=30,
            impersonate="chrome",
        )
        data = resp.json() if resp is not None else {}
    except Exception as exc:
        raise Exception(f"CapMonster createTask 网络错误: {exc}") from exc

    if not isinstance(data, dict):
        raise Exception(f"CapMonster createTask 无效响应: {data!r}")
    if int(data.get("errorId") or 0) != 0:
        err = data.get("errorCode") or data.get("errorDescription") or data
        raise Exception(f"CapMonster createTask 失败: {err}")

    task_id = data.get("taskId")
    if task_id is None:
        raise Exception(f"CapMonster 未返回 taskId: {data}")

    if log_callback:
        log_callback(f"[*] CapMonster taskId={task_id}，轮询结果（最多 {timeout_s}s）…")

    deadline = time.time() + timeout_s
    last_log = 0.0
    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        sleep_with_cancel(poll, cancel_callback)
        try:
            r2 = requests.post(
                f"{base}/getTaskResult",
                json={"clientKey": api_key, "taskId": task_id},
                timeout=30,
                impersonate="chrome",
            )
            body = r2.json() if r2 is not None else {}
        except Exception as exc:
            if log_callback and time.time() - last_log >= 5:
                last_log = time.time()
                log_callback(f"[Debug] CapMonster getTaskResult 网络: {exc}")
            continue

        if not isinstance(body, dict):
            continue
        if int(body.get("errorId") or 0) != 0:
            err = body.get("errorCode") or body.get("errorDescription") or body
            raise Exception(f"CapMonster 解题失败: {err}")

        status = str(body.get("status") or "").lower()
        if status == "processing":
            if log_callback and time.time() - last_log >= 8:
                last_log = time.time()
                rem = deadline - time.time()
                log_callback(f"[Debug] CapMonster processing… 剩余 {rem:.0f}s")
            continue
        if status == "ready":
            solution = body.get("solution") or {}
            token = ""
            if isinstance(solution, dict):
                token = (
                    solution.get("token")
                    or solution.get("gRecaptchaResponse")
                    or solution.get("cf-turnstile-response")
                    or ""
                )
            token = str(token or "").strip()
            if len(token) < 50:
                raise Exception(f"CapMonster ready 但 token 异常: {solution!r}")
            if log_callback:
                log_callback(f"[*] CapMonster 已解出 Turnstile token，长度={len(token)}")
            return token

        if log_callback and time.time() - last_log >= 8:
            last_log = time.time()
            log_callback(f"[Debug] CapMonster 未知状态: {body}")

    raise Exception(f"CapMonster 超时（{timeout_s}s）未拿到 token，taskId={task_id}")


def _turnstile_point_via_cdp_iframe():
    """
    Pierce closed shadow via CDP DOM.getDocument(pierce=True) and locate the
    Turnstile iframe (id=cf-chl-widget-*, challenges.cloudflare.com/...).
    Return left-side checkbox client coords or None.
    """
    global page
    if page is None:
        return None
    try:
        doc = page.run_cdp("DOM.getDocument", depth=-1, pierce=True)
        root = doc.get("root") if isinstance(doc, dict) else None
        if not root:
            return None

        target_backend = None

        def walk(node):
            nonlocal target_backend
            if not node or target_backend is not None:
                return
            name = str(node.get("nodeName") or "").upper()
            attrs = node.get("attributes") or []
            amap = {}
            for i in range(0, len(attrs) - 1, 2):
                amap[str(attrs[i])] = str(attrs[i + 1])
            src = amap.get("src", "")
            nid = amap.get("id", "")
            title = amap.get("title", "")
            if name == "IFRAME" and (
                "challenges.cloudflare.com" in src
                or "turnstile" in src
                or nid.startswith("cf-chl-widget")
                or "cloudflare" in title.lower()
                or "cabaran" in title.lower()  # Malay: "Widget mengandungi cabaran..."
            ):
                target_backend = node.get("backendNodeId") or node.get("nodeId")
                return
            for c in node.get("children") or []:
                walk(c)
            for s in node.get("shadowRoots") or []:
                walk(s)
            if node.get("contentDocument"):
                walk(node["contentDocument"])

        walk(root)
        if not target_backend:
            return None

        # Resolve to nodeId for box model
        try:
            resolved = page.run_cdp("DOM.resolveNode", backendNodeId=int(target_backend))
            # prefer getBoxModel by backendNodeId if supported
        except Exception:
            resolved = None

        box = None
        for kwargs in (
            {"backendNodeId": int(target_backend)},
            {},
        ):
            try:
                if kwargs:
                    box = page.run_cdp("DOM.getBoxModel", **kwargs)
                else:
                    # fallback: push node id via requestNode if we only have object
                    if not resolved:
                        continue
                    obj_id = (resolved.get("object") or {}).get("objectId")
                    if not obj_id:
                        continue
                    req = page.run_cdp("DOM.requestNode", objectId=obj_id)
                    nid = req.get("nodeId") if isinstance(req, dict) else None
                    if not nid:
                        continue
                    box = page.run_cdp("DOM.getBoxModel", nodeId=int(nid))
                if box:
                    break
            except Exception:
                box = None
                continue

        if not isinstance(box, dict):
            return None
        model = box.get("model") or box
        content = model.get("content") or model.get("border") or []
        if len(content) < 8:
            return None
        # content = [x0,y0, x1,y1, x2,y2, x3,y3] CSS pixels
        xs = content[0::2]
        ys = content[1::2]
        left, right = min(xs), max(xs)
        top, bottom = min(ys), max(ys)
        w, h = right - left, bottom - top
        if w < 50 or h < 20:
            return None
        return {
            "x": left + min(30, max(14, w * 0.07)),
            "y": top + h / 2,
            "w": w,
            "h": h,
            "tag": "IFRAME",
            "source": "cdp-pierce",
        }
    except Exception:
        return None


def _click_turnstile_widget(log_callback=None):
    """
    Click the Turnstile checkbox (not Complete sign up).

    accounts.x.ai puts the challenge iframe in a *closed* shadow root, so
    querySelector never sees it. Reliable path:
      1. Detect sized host of cf-turnstile-response (light DOM) → coords
      2. CDP pierce for iframe box model → coords
      3. Playwright mouse at those coords + CF challenge frames
      4. CDP Input.dispatchMouseEvent
      5. Drission / DOM fallbacks
    """
    global page
    if page is None:
        return "no-page"

    point = _turnstile_click_point()
    if not point:
        point = _turnstile_point_via_cdp_iframe()
        if point and log_callback:
            log_callback(
                f"[Debug] Turnstile CDP pierce iframe @ "
                f"{int(point['x'])},{int(point['y'])} ({int(point.get('w',0))}x{int(point.get('h',0))})"
            )

    # 1) Playwright attached to Roxy (or Browser Use page._page)
    pw_page = None
    try:
        pw_page = _attach_playwright_cdp(log_callback=None)
    except Exception:
        pw_page = None
    if pw_page is None:
        pw_page = getattr(page, "_page", None)

    if pw_page is not None:
        try:
            # a) Frame URL / name matching CF challenge (closed shadow still exposes frame tree)
            for frame in pw_page.frames:
                url = (frame.url or "").lower()
                name = (frame.name or "").lower()
                is_cf = any(
                    k in url or k in name
                    for k in (
                        "turnstile",
                        "challenges.cloudflare",
                        "cf-chl",
                        "cdn-cgi/challenge",
                    )
                )
                # blank child frames of CF also need a try
                if not is_cf and url not in ("", "about:blank"):
                    continue
                if not is_cf and url in ("", "about:blank"):
                    # only probe blank if we already know a challenge exists
                    if not point:
                        continue
                for sel in (
                    "input[type='checkbox']",
                    "[role='checkbox']",
                    "label",
                    ".ctp-checkbox-label",
                    "#challenge-stage input",
                    "#challenge-stage",
                    "body",
                ):
                    try:
                        fl = frame.locator(sel).first
                        if fl.count() == 0:
                            continue
                        if not fl.is_visible(timeout=500):
                            continue
                        box = fl.bounding_box()
                        if box and box.get("width", 0) >= 8:
                            jx = box["x"] + min(24, max(10, box["width"] * 0.1))
                            jy = box["y"] + box["height"] / 2
                            pw_page.mouse.move(jx, jy, steps=6)
                            time.sleep(0.05)
                            pw_page.mouse.click(jx, jy, delay=40)
                        else:
                            fl.click(timeout=1500, force=True)
                        if log_callback:
                            log_callback(
                                f"[Debug] Turnstile frame click: {sel} @ {(url or name or 'blank')[:70]}"
                            )
                        return f"pw-frame:{sel}"
                    except Exception:
                        continue

            # b) frame_locator by title (a11y: "Widget mengandungi cabaran keselamatan Cloudflare")
            for title_re in (
                re.compile(r"cloudflare|turnstile|cabaran|keselamatan|challenge", re.I),
            ):
                try:
                    fl = pw_page.frame_locator(f'iframe[title="{title_re.pattern}"]')
                except Exception:
                    fl = None
                try:
                    # title contains match
                    iframe_loc = pw_page.locator("iframe").filter(has=pw_page.locator(":scope"))
                    # Prefer get_by_title
                    host = pw_page.get_by_title(
                        re.compile(r"cloudflare|cabaran|turnstile|challenge", re.I)
                    ).first
                    if host.count() > 0:
                        box = host.bounding_box()
                        if box:
                            jx = box["x"] + min(28, max(12, box["width"] * 0.08))
                            jy = box["y"] + box["height"] / 2
                            pw_page.mouse.move(jx, jy, steps=8)
                            time.sleep(0.05)
                            pw_page.mouse.click(jx, jy, delay=50)
                            if log_callback:
                                log_callback(
                                    f"[Debug] Turnstile title-iframe click: {int(jx)},{int(jy)}"
                                )
                            return f"pw-title-iframe:{int(jx)},{int(jy)}"
                except Exception:
                    pass

            for sel in (
                'iframe[id^="cf-chl-widget"]',
                'iframe[src*="challenges.cloudflare.com"]',
                'iframe[src*="turnstile"]',
                'iframe[title*="Cloudflare" i]',
                'iframe[title*="cabaran" i]',
                ".cf-turnstile",
                "[data-sitekey]",
            ):
                try:
                    loc = pw_page.locator(sel).first
                    if loc.count() > 0:
                        box = None
                        try:
                            box = loc.bounding_box(timeout=800)
                        except Exception:
                            box = None
                        if box and box.get("width", 0) >= 50:
                            jx = box["x"] + min(28, max(12, box["width"] * 0.08))
                            jy = box["y"] + box["height"] / 2
                            pw_page.mouse.move(jx, jy, steps=8)
                            time.sleep(0.05)
                            pw_page.mouse.click(jx, jy, delay=50)
                            if log_callback:
                                log_callback(f"[Debug] Turnstile host click: {sel}")
                            return f"pw-host:{sel}"
                except Exception:
                    continue

            # c) Coordinate click from detector / CDP pierce (most reliable for closed shadow)
            if point:
                try:
                    jx, jy = float(point["x"]), float(point["y"])
                    pw_page.mouse.move(jx, jy, steps=8)
                    time.sleep(0.08)
                    pw_page.mouse.click(jx, jy, delay=50)
                    if log_callback:
                        src = point.get("source") or "?"
                        log_callback(
                            f"[Debug] Turnstile pw-coord click: {int(jx)},{int(jy)} src={src}"
                        )
                    return f"pw-coord:{int(jx)},{int(jy)}:{point.get('source', '')}"
                except Exception:
                    pass
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Playwright Turnstile 点击失败: {exc}")

    # 2) CDP trusted mouse at checkbox coords
    if point:
        try:
            detail = _cdp_mouse_click_at(point["x"], point["y"], steps=6)
            if log_callback:
                log_callback(f"[Debug] Turnstile CDP click: {detail}")
            return f"cdp:{detail}"
        except Exception as exc:
            if log_callback:
                log_callback(f"[Debug] Turnstile CDP 失败: {exc}")

    # 3) Click sized host of response field via JS rect + CDP (no iframe needed)
    try:
        host_pt = page.run_js(
            """
const cf = document.querySelector(
  'input[name="cf-turnstile-response"], input[id*="cf-chl-widget"]'
);
if (!cf) return null;
let n = cf.parentElement, best = null;
for (let i = 0; i < 8 && n; i++) {
  const r = n.getBoundingClientRect();
  if (r.width >= 200 && r.width <= 520 && r.height >= 40 && r.height <= 100) {
    best = { x: r.left + Math.min(30, Math.max(14, r.width * 0.07)), y: r.top + r.height / 2, w: r.width, h: r.height };
    break;
  }
  n = n.parentElement;
}
if (!best) {
  const btn = Array.from(document.querySelectorAll('button')).find(b =>
    /complete\\s*sign\\s*up/i.test((b.innerText || '').trim()));
  if (btn) {
    const br = btn.getBoundingClientRect();
    best = { x: br.left + 28, y: br.top - 40, w: br.width, h: 65 };
  }
}
return best;
            """
        )
        if isinstance(host_pt, dict) and host_pt.get("x") is not None:
            detail = _cdp_mouse_click_at(host_pt["x"], host_pt["y"], steps=6)
            if log_callback:
                log_callback(f"[Debug] Turnstile host-rect CDP: {detail} pt={host_pt}")
            return f"cdp-host:{detail}"
    except Exception as exc:
        if log_callback:
            log_callback(f"[Debug] Turnstile host-rect 失败: {exc}")

    # 4) DrissionPage: click parent of response field (coordinates)
    try:
        challenge_input = page.ele("@name=cf-turnstile-response", timeout=0.5)
        if challenge_input:
            try:
                wrapper = challenge_input.parent
                for _ in range(4):
                    if wrapper is None:
                        break
                    try:
                        # Drission click center of sized wrapper
                        rect = getattr(wrapper, "rect", None)
                        if rect:
                            # try methods that exist across versions
                            pass
                        wrapper.click(by_js=False)
                        return "dp-parent-click"
                    except Exception:
                        try:
                            wrapper = wrapper.parent
                        except Exception:
                            break
            except Exception:
                pass
            # classic shadow path (open shadow only)
            try:
                wrapper = challenge_input.parent
                iframe = None
                try:
                    iframe = wrapper.shadow_root.ele("tag:iframe")
                except Exception:
                    iframe = None
                if iframe:
                    try:
                        body_sr = iframe.ele("tag:body").shadow_root
                        btn = body_sr.ele("tag:input")
                        if btn:
                            btn.click()
                            return "dp-shadow-input"
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass

    # 5) DOM fallback (last resort, often isTrusted=false)
    try:
        clicked = page.run_js(
            """
const cf = document.querySelector('input[name="cf-turnstile-response"], input[id*="cf-chl-widget"]');
let target = null;
if (cf) {
  let n = cf.parentElement;
  for (let i = 0; i < 8 && n; i++) {
    const r = n.getBoundingClientRect();
    if (r.width >= 200 && r.height >= 40 && r.height <= 100) { target = n; break; }
    n = n.parentElement;
  }
}
if (!target) {
  target = document.querySelector('.cf-turnstile, [data-sitekey], iframe[src*="turnstile"]');
}
if (!target) return 'no-host';
target.scrollIntoView({block:'center'});
const rect = target.getBoundingClientRect();
const x = rect.left + Math.min(28, Math.max(12, rect.width*0.08));
const y = rect.top + rect.height/2;
const opts = {bubbles:true, cancelable:true, view:window, clientX:x, clientY:y, button:0};
for (const type of ['pointerdown','mousedown','pointerup','mouseup','click']) {
  const Ctor = type.startsWith('pointer') ? PointerEvent : MouseEvent;
  target.dispatchEvent(new Ctor(type, opts));
}
try { target.click(); } catch(e) {}
return 'dom-click:' + Math.round(x) + ',' + Math.round(y);
            """
        )
        return str(clicked or "dom")
    except Exception as exc:
        return f"err:{type(exc).__name__}"


def ensure_turnstile_before_submit(
    log_callback=None,
    cancel_callback=None,
    timeout=45,
    settle_seconds=8.0,
):
    """
    After profile fill: WAIT for Turnstile to appear, solve it, THEN allow Complete.

    Never rush Complete. Flow:
      1. Human settle pause (widget often paints a few seconds after fill)
      2. Poll for token / checkbox / cf-turnstile-response field
      3. If challenge present (or empty CF field) → click checkbox until token
      4. Only return when token ready, or settle window ends with zero CF signals
    """
    global page
    if page is None:
        return ""

    token = _read_turnstile_token()
    if len(token) >= 80:
        if log_callback:
            log_callback(f"[*] Turnstile 已有 token（长度={len(token)}），可提交")
        return token

    settle = max(float(settle_seconds or 8.0), 3.0)
    if log_callback:
        log_callback(
            f"[*] 资料已填完 — 先等待最多 {settle:.0f}s 看 Turnstile 是否出现，"
            "不会立刻点 Complete sign up"
        )

    # Phase A: passive watch (do not click Complete; optionally soft-click checkbox if already visible)
    paint_deadline = time.time() + settle
    saw_challenge = False
    last_log = 0.0
    while time.time() < paint_deadline:
        raise_if_cancelled(cancel_callback)
        token = _read_turnstile_token()
        if len(token) >= 80:
            if log_callback:
                log_callback(f"[*] Turnstile 已通过（等待期间），token长度={len(token)}")
            return token

        state = _probe_turnstile_state() or {}
        # Empty CF response field almost always means challenge is coming / present
        has_field = bool(state.get("hasCfInput"))
        widgets = int(state.get("widgets") or 0)
        human = bool(state.get("visibleHuman") or state.get("textHits") or state.get("point"))
        if has_field or widgets > 0 or human or _turnstile_needs_solve():
            saw_challenge = True
            # Start interacting as soon as we see a signal (don't wait full settle idle)
            if log_callback and time.time() - last_log >= 2.0:
                last_log = time.time()
                log_callback(
                    f"[*] 已出现人机验证信号 field={has_field} widgets={widgets} "
                    f"human={human} — 开始点选 Turnstile（仍不点 Complete）"
                )
            break
        remaining = paint_deadline - time.time()
        if log_callback and time.time() - last_log >= 2.5:
            last_log = time.time()
            log_callback(f"[Debug] 等待 Turnstile 出现… 剩余 {remaining:.1f}s state={state}")
        sleep_with_cancel(0.5, cancel_callback)

    state = _probe_turnstile_state() or {}
    if log_callback:
        log_callback(f"[Debug] 等待结束后 Turnstile 探测: {state}")

    token = _read_turnstile_token()
    if len(token) >= 80:
        return token

    needs = (
        saw_challenge
        or _turnstile_needs_solve()
        or bool(state.get("hasCfInput"))  # empty field → must solve, never skip
        or bool(state.get("visibleHuman"))
        or int(state.get("widgets") or 0) > 0
        or bool(state.get("point"))
        or bool(state.get("textHits"))
    )

    if not needs:
        # True no-challenge path (rare). Still give one short extra beat.
        if log_callback:
            log_callback("[*] 等待窗口内未见 Turnstile 字段/文案，再确认 2s…")
        sleep_with_cancel(2.0, cancel_callback)
        state2 = _probe_turnstile_state() or {}
        token = _read_turnstile_token()
        if len(token) >= 80:
            return token
        if (
            state2.get("hasCfInput")
            or state2.get("visibleHuman")
            or int(state2.get("widgets") or 0) > 0
        ):
            needs = True
            state = state2
        else:
            if log_callback:
                log_callback("[*] 确认无 Turnstile，才允许 Complete sign up")
            return token

    if log_callback:
        log_callback(
            "[*] 先完成 Cloudflare Turnstile（CapMonster / 本地点击），"
            "通过后再点 Complete"
        )

    try:
        token = getTurnstileToken(
            log_callback=log_callback,
            cancel_callback=cancel_callback,
            timeout=timeout,
        )
    except Exception as exc:
        if log_callback:
            log_callback(f"[!] Turnstile 未通过: {exc}")
        raise

    if token:
        _inject_turnstile_token(token, log_callback=log_callback)
        # Brief pause so SPA registers solved state before Complete
        sleep_with_cancel(0.8, cancel_callback)
    return token or _read_turnstile_token()


def getTurnstileToken(log_callback=None, cancel_callback=None, timeout=35):
    """
    Obtain Turnstile response token.

    Priority:
      1. Already present in page
      2. CapMonster Cloud (TurnstileTaskProxyless) when api key is set
      3. Optional local checkbox click fallback (usually weak vs managed CF)
    """
    global page
    if page is None:
        raise Exception("页面未就绪，无法执行 Turnstile")

    token = _read_turnstile_token()
    if len(token) >= 80:
        if log_callback:
            log_callback(f"[*] Turnstile 已有 token，长度={len(token)}")
        return token

    # --- CapMonster Cloud (preferred when key present) ---
    if capmonster_is_enabled():
        cm_timeout = int(config.get("capmonster_timeout") or 120)
        cm_timeout = max(cm_timeout, 45)
        # Caller timeout can extend but not shrink below config default
        if timeout:
            cm_timeout = max(cm_timeout, int(timeout))
        try:
            token = solve_turnstile_capmonster(
                log_callback=log_callback,
                cancel_callback=cancel_callback,
                timeout=cm_timeout,
            )
            if len(token) >= 50:
                _inject_turnstile_token(token, log_callback=log_callback)
                # re-read in case SPA rewrote field
                again = _read_turnstile_token()
                if len(again) >= 50:
                    return again
                return token
        except Exception as exc:
            if log_callback:
                log_callback(f"[!] CapMonster 失败: {exc}")
            if not config.get("capmonster_fallback_click", True):
                raise
            if log_callback:
                log_callback("[*] CapMonster 失败，回退本地点击 Turnstile（成功率较低）…")
    elif log_callback:
        log_callback(
            "[*] 未配置 capmonster_api_key — 使用本地点击 "
            "（managed Turnstile 通常会 Pengesahan gagal）"
        )

    # --- Local click fallback ---
    timeout_s = max(int(timeout or 35), 5)
    # Don't burn too long on hopeless CDP spam when CapMonster was available
    if capmonster_is_enabled():
        timeout_s = min(timeout_s, 20)
    started = time.time()
    deadline = started + timeout_s
    last_click = 0.0
    last_diag = 0.0
    attempt = 0
    clicks = 0
    max_clicks = 4  # avoid hammering into "Pengesahan gagal"

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        attempt += 1
        token = _read_turnstile_token()
        if len(token) >= 80:
            if log_callback:
                log_callback(f"[*] Turnstile 已通过（本地），token长度={len(token)}")
            return token

        now = time.time()
        if clicks < max_clicks and now - last_click >= 4.5:
            detail = _click_turnstile_widget(log_callback=log_callback)
            last_click = now
            clicks += 1
            if log_callback:
                log_callback(f"[Debug] Turnstile 本地点击 {clicks}/{max_clicks}: {detail}")

        if log_callback and now - last_diag >= 8:
            last_diag = now
            state = _probe_turnstile_state()
            log_callback(f"[Debug] Turnstile 状态: {state}")

        sleep_with_cancel(1.0, cancel_callback)

    state = _probe_turnstile_state()
    raise Exception(f"Turnstile 获取 token 失败，最后状态={state}")

def build_profile():
    given_name_pool = [
        "Neo", "Ethan", "Liam", "Noah", "Lucas", "Mason", "Ryan", "Leo",
        "Owen", "Aiden", "Elio", "Aron", "Ivan", "Nolan", "Evan", "Kai",
        "Caleb", "Adam", "Ezra", "Miles", "Logan", "Carter", "Hunter", "Jason",
        "Brian", "Dylan", "Alex", "Colin", "Blake", "Gavin", "Henry", "Julian",
        "Kevin", "Louis", "Marcus", "Nathan", "Oscar", "Peter", "Quinn", "Robin",
        "Simon", "Tristan", "Victor", "Wesley", "Xavier", "Yuri", "Zane", "Felix",
        "Aaron", "Damian",
    ]
    family_name_pool = [
        "Lin", "Wang", "Zhao", "Liu", "Chen", "Zhang", "Xu", "Sun",
        "Guo", "He", "Yang", "Wu", "Zhou", "Tang", "Qin", "Shi",
        "Fang", "Peng", "Cao", "Deng", "Fan", "Fu", "Gao", "Han",
        "Hu", "Jiang", "Kong", "Lu", "Ma", "Nie", "Pan", "Qiao",
        "Ren", "Shao", "Tian", "Xie", "Yan", "Yao", "Yu", "Zeng",
        "Bai", "Duan", "Hou", "Jin", "Kang", "Luo", "Mao", "Song",
        "Wei", "Xiong",
    ]
    given_name = random.choice(given_name_pool)
    family_name = random.choice(family_name_pool)
    password = "N" + secrets.token_hex(4) + "!a7#" + secrets.token_urlsafe(6)
    return given_name, family_name, password


def fill_profile_and_submit(timeout=120, log_callback=None, cancel_callback=None):
    given_name, family_name, password = build_profile()
    deadline = time.time() + timeout
    form_filled_once = False
    wait_cf_since = None
    last_cf_retry_at = 0.0
    # Capture turnstile.render callbacks BEFORE the widget mounts on this step.
    _install_turnstile_callback_hook(log_callback=log_callback)

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        if not _page_is_alive():
            raise AccountRetryNeeded("填写资料时页面/会话已关闭，重试账号")
        if not form_filled_once:
            try:
                filled = page.run_js(
                """
const givenName = arguments[0];
const familyName = arguments[1];
const password = arguments[2];

function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function pickInput(selector) {
    return Array.from(document.querySelectorAll(selector)).find((node) => {
        return isVisible(node) && !node.disabled && !node.readOnly;
    }) || null;
}

function setInputValue(input, value) {
    if (!input) return false;
    input.focus();
    input.click();
    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
    const tracker = input._valueTracker;
    if (tracker) tracker.setValue('');
    if (nativeSetter) nativeSetter.call(input, value);
    else input.value = value;
    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: value, inputType: 'insertText' }));
    input.dispatchEvent(new Event('change', { bubbles: true }));
    input.blur();
    return String(input.value || '').trim() === String(value || '').trim();
}

const givenInput = pickInput('input[data-testid="givenName"], input[name="givenName"], input[autocomplete="given-name"], input[aria-label*="名"]');
const familyInput = pickInput('input[data-testid="familyName"], input[name="familyName"], input[autocomplete="family-name"], input[aria-label*="姓"]');
const passwordInput = pickInput('input[data-testid="password"], input[name="password"], input[type="password"], input[autocomplete="new-password"]');

if (!givenInput || !familyInput || !passwordInput) return 'not-ready';

const ok1 = setInputValue(givenInput, givenName);
const ok2 = setInputValue(familyInput, familyName);
const ok3 = setInputValue(passwordInput, password);

if (!ok1 || !ok2 || !ok3) return 'fill-failed';

const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = (node.innerText || node.textContent || '').replace(/\\s+/g, '').toLowerCase();
    // EN / CN / JP complete-signup labels
    return (
      t.includes('完成注册') || t.includes('创建账户') || t.includes('创建帐户') ||
      t.includes('signup') || t.includes('createaccount') || t.includes('create account') ||
      t.includes('completesignup') || t.includes('completeyoursignup') ||
      t.includes('登録を完了') || t.includes('登録完了') || t.includes('アカウントを作成') ||
      t.includes('登録する') || t.includes('完了') || t.includes('続行') || t.includes('続ける') ||
      t.includes('submit') || t.includes('continue') || t.includes('next')
    );
});

// Fill only — Turnstile handling is done in Python BEFORE Complete click.
// Return ready-to-submit when fields are filled; never click submit here.
if (submitBtn) {
    submitBtn.setAttribute('data-grok-complete-signup', '1');
    return 'ready-to-submit';
}
return 'filled-no-submit';
            """,
                given_name,
                family_name,
                password,
            )
            except Exception as page_exc:
                err = str(page_exc)
                if "closed" in err.lower() or "Target page" in err or "Target closed" in err:
                    raise AccountRetryNeeded(f"填写资料时页面关闭: {err[:120]}")
                raise

            if filled in ("ready-to-submit", "filled-no-submit"):
                form_filled_once = True
                if log_callback:
                    log_callback(
                        f"[*] 资料已写入: {given_name} {family_name} — "
                        "停顿几秒观察 Turnstile，不会立刻点 Complete"
                    )
                # Let CF widget paint after password fill (often 2–6s delayed)
                sleep_with_cancel(2.5, cancel_callback)
            elif filled == "fill-failed" and log_callback:
                log_callback("[Debug] 资料输入失败，重试中...")
                sleep_with_cancel(0.5, cancel_callback)
                continue
            elif filled == "not-ready":
                sleep_with_cancel(0.5, cancel_callback)
                continue

        # ALWAYS wait + solve Turnstile BEFORE Complete (never rush the black button).
        # Install render-hook early so CapMonster inject can fire SPA callback.
        _install_turnstile_callback_hook(log_callback=log_callback)
        try:
            ensure_turnstile_before_submit(
                log_callback=log_callback,
                cancel_callback=cancel_callback,
                # CapMonster cloud solve often needs 30–90s; local click is shorter
                timeout=max(55, int(config.get("capmonster_timeout") or 120) if capmonster_is_enabled() else 55),
                settle_seconds=8.0,
            )
        except Exception as cf_exc:
            if log_callback:
                log_callback(f"[Debug] Turnstile 未就绪，暂不点 Complete: {cf_exc}")
            sleep_with_cancel(1.0, cancel_callback)
            continue

        # Hard block: still unsolved CF field / human checkbox → do not mark Complete
        if _turnstile_needs_solve() and len(_read_turnstile_token()) < 80:
            if log_callback:
                log_callback("[*] Turnstile 仍未通过 — 继续等待/点击验证框，跳过 Complete")
            try:
                _click_turnstile_widget(log_callback=log_callback)
            except Exception:
                pass
            sleep_with_cancel(1.2, cancel_callback)
            continue

        submit_state = page.run_js(
            r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
function isSignupSubmit(node) {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    if (t.includes('goback') || t.includes('go back') || t.includes('返回') || t.includes('戻る')) return false;
    if (t.includes('email') && !t.includes('complete')) return false;
    return (
      t.includes('完成注册') || t.includes('创建账户') || t.includes('创建帐户') ||
      t.includes('completesignup') || t.includes('completeyoursignup') ||
      t.includes('complete sign up') || t.includes('createaccount') || t.includes('create account') ||
      t.includes('登録を完了') || t.includes('登録完了') || t.includes('アカウントを作成') ||
      t.includes('登録する') ||
      (t.includes('sign up') && !t.includes('with')) ||
      t.includes('submit') || t.includes('continue') || t.includes('next') ||
      t.includes('完了') || t.includes('続行') || t.includes('続ける')
    );
}

const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find(isSignupSubmit);

if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'no-submit-button:' + visibleTexts;
}
// Mark only — actual click uses CDP (trusted). JS click is a no-op on Roxy/x.ai.
submitBtn.setAttribute('data-grok-complete-signup', '1');
submitBtn.focus();
return 'ready-for-cdp-submit';
            """
        )

        if submit_state == "ready-for-cdp-submit":
            # Final guard: never Complete while CF unsolved
            token_now = _read_turnstile_token()
            if len(token_now) < 80 and _turnstile_needs_solve():
                if log_callback:
                    log_callback("[*] Complete 前再次确认：Turnstile 未通过，继续解验证（不点 Complete）")
                try:
                    ensure_turnstile_before_submit(
                        log_callback=log_callback,
                        cancel_callback=cancel_callback,
                        timeout=40,
                        settle_seconds=3.0,
                    )
                except Exception as cf_exc:
                    if log_callback:
                        log_callback(f"[Debug] Complete 前 Turnstile 仍失败: {cf_exc}")
                    sleep_with_cancel(1.0, cancel_callback)
                    continue
                if len(_read_turnstile_token()) < 80 and _turnstile_needs_solve():
                    sleep_with_cancel(1.0, cancel_callback)
                    continue
            # CapMonster token can vanish from the hidden field before click — re-inject.
            _ensure_turnstile_token_present(log_callback=log_callback)
            if _LAST_TURNSTILE_TOKEN:
                _fire_turnstile_success_callbacks(
                    _LAST_TURNSTILE_TOKEN, log_callback=log_callback
                )
            # Give React a beat to enable Complete after callback.
            sleep_with_cancel(0.6, cancel_callback)
            if log_callback:
                tok_len = len(_read_turnstile_token() or _LAST_TURNSTILE_TOKEN)
                log_callback(f"[*] Turnstile OK（token长度={tok_len}）— 现在才点 Complete sign up")
            _diagnose_complete_button(log_callback=log_callback)
            # Prefer a single trusted pointer click. Extra form.requestSubmit /
            # fiber onSubmit can race the real handler and cancel the RPC.
            cdp_detail = _cdp_click_complete_signup(log_callback=log_callback)
            form_detail = "deferred"
            # Only fall back to form/fiber submit if pointer path missed the button.
            if str(cdp_detail).startswith("cdp-complete:err") or cdp_detail == "cdp-complete:no-btn":
                form_detail = _form_request_submit_complete(log_callback=log_callback)
            _diagnose_complete_button(log_callback=log_callback)
            # Give AuthManagement a few seconds before wait_for_sso starts re-clicking.
            sleep_with_cancel(2.5, cancel_callback)
            if log_callback:
                log_callback(
                    f"[*] 已填写注册资料并提交: {given_name} {family_name} "
                    f"({cdp_detail}|form={form_detail})"
                )
            return {"given_name": given_name, "family_name": family_name, "password": password}

        if submit_state == "submitted":
            if log_callback:
                log_callback(f"[*] 已填写注册资料并提交: {given_name} {family_name}")
            return {"given_name": given_name, "family_name": family_name, "password": password}
        wait_cf_since = None
        if isinstance(submit_state, str) and submit_state.startswith("no-submit-button") and log_callback:
            visible_buttons = submit_state.split(":", 1)[1] if ":" in submit_state else ""
            suffix = f" 可见按钮: {visible_buttons}" if visible_buttons else ""
            log_callback(f"[Debug] 未找到提交按钮，继续等待页面稳定...{suffix}")

        sleep_with_cancel(0.5, cancel_callback)

    raise Exception("最终注册页资料填写失败")


def _final_page_diagnose():
    """Collect final signup page diagnostics for stuck SSO waits."""
    global page
    if page is None:
        return {}
    try:
        return page.run_js(
            r"""
function isVisible(node) {
  if (!node) return false;
  const s = getComputedStyle(node);
  if (s.display==='none'||s.visibility==='hidden'||s.opacity==='0') return false;
  const r = node.getBoundingClientRect();
  return r.width>0 && r.height>0;
}
const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const inputs = Array.from(document.querySelectorAll('input,select,textarea'))
  .filter(isVisible)
  .map((n) => ({
    type: n.getAttribute('type') || n.tagName.toLowerCase(),
    name: n.getAttribute('name') || '',
    testid: n.getAttribute('data-testid') || '',
    ac: n.getAttribute('autocomplete') || '',
    ph: (n.getAttribute('placeholder') || '').slice(0, 40),
    valLen: String(n.value || '').length,
  }))
  .slice(0, 12);
const buttons = Array.from(document.querySelectorAll('button, [role="button"], input[type="submit"]'))
  .filter(isVisible)
  .map((n) => (n.innerText || n.textContent || n.getAttribute('value') || '').replace(/\s+/g,' ').trim())
  .filter(Boolean)
  .slice(0, 10);
const errors = Array.from(document.querySelectorAll('[role="alert"], .error, [class*="error" i], [data-testid*="error" i], p, span, div'))
  .filter(isVisible)
  .map((n) => (n.innerText || n.textContent || '').replace(/\s+/g,' ').trim())
  .filter((t) => t && t.length < 160 && /(error|invalid|required|failed|captcha|challenge|blocked|try again|問題|エラー|無効|必須|失敗|認証)/i.test(t))
  .slice(0, 6);
return {
  url: location.href,
  title: document.title,
  tokenLen: String((cfInput && cfInput.value) || '').trim().length,
  widgets: document.querySelectorAll('iframe[src*="turnstile"], iframe[src*="challenges.cloudflare"], .cf-turnstile, [data-sitekey]').length,
  inputs,
  buttons,
  errors,
  bodySnippet: (document.body && document.body.innerText || '').replace(/\s+/g,' ').trim().slice(0, 220),
};
            """
        ) or {}
    except Exception as exc:
        return {"error": str(exc)[:160]}


def _playwright_click_signup_submit(log_callback=None):
    """Prefer real Playwright mouse click on final signup submit (React-friendly)."""
    global page
    # page._page is never set on DrissionPage; attach Playwright over Roxy CDP.
    pw_page = _attach_playwright_cdp(log_callback=None)
    if pw_page is None:
        return "no-pw"
    # Prefer exact final-step labels first (avoid matching earlier "Sign up with email")
    labels = [
        "Complete sign up",
        "Complete signup",
        "Complete your sign up",
        "Create account",
        "Create your account",
        "完成注册",
        "创建账户",
        "登録を完了",
        "登録する",
        "アカウントを作成",
        "Sign up",
        "Continue",
        "Next",
        "続行",
        "続ける",
    ]
    for text in labels:
        try:
            loc = pw_page.get_by_role("button", name=re.compile(rf"^{re.escape(text)}$", re.I)).first
            if loc.count() > 0 and loc.is_visible(timeout=400):
                loc.scroll_into_view_if_needed(timeout=2000)
                loc.click(timeout=3000)
                if log_callback:
                    log_callback(f"[Debug] Playwright 提交点击: exact-role={text}")
                return f"pw-exact-role:{text}"
        except Exception:
            continue
    for text in labels:
        try:
            loc = pw_page.get_by_role("button", name=re.compile(re.escape(text), re.I)).first
            if loc.count() > 0 and loc.is_visible(timeout=400):
                # Skip "Sign up with email" style buttons on final page
                try:
                    name = (loc.inner_text(timeout=500) or "").strip().lower()
                except Exception:
                    name = ""
                if "email" in name and "complete" not in name:
                    continue
                loc.scroll_into_view_if_needed(timeout=2000)
                loc.click(timeout=3000)
                if log_callback:
                    log_callback(f"[Debug] Playwright 提交点击: role=button name~{text}")
                return f"pw-role:{text}"
        except Exception:
            continue
    try:
        loc = pw_page.locator('button[type="submit"]').first
        if loc.count() > 0 and loc.is_visible(timeout=500):
            loc.click(timeout=3000)
            return "pw-submit-type"
    except Exception:
        pass
    return "pw-miss"


def _log_network_failures(log_callback=None):
    global browser
    if log_callback is None or browser is None:
        return
    getter = getattr(browser, "recent_network_failures", None)
    if not callable(getter):
        return
    try:
        events = getter(limit=6)
        if events:
            log_callback(f"[Debug] 近期注册相关网络: {events}")
    except Exception:
        pass


def wait_for_sso_cookie(timeout=120, log_callback=None, cancel_callback=None):
    deadline = time.time() + timeout
    last_seen_names = set()
    last_submit_retry = 0.0
    last_cf_retry_at = 0.0
    last_diag_at = 0.0
    final_no_submit_state = ""
    final_no_submit_since = None
    final_no_submit_timeout = 25
    clicked_no_token_count = 0
    # First Complete already happened in fill_profile; wait before re-clicking.
    first_complete_grace_until = time.time() + 18.0

    while time.time() < deadline:
        raise_if_cancelled(cancel_callback)
        try:
            # Soft reattach only — never restart_browser here (would wipe SSO cookies).
            if not _page_is_alive():
                refresh_active_page(allow_restart=False)
                if not _page_is_alive():
                    # Still poll cookies via CDP; navigation often drops run_js briefly.
                    cookies = _collect_browser_cookies()
                    for item in cookies:
                        if isinstance(item, dict):
                            name = str(item.get("name", "")).strip()
                            value = str(item.get("value", "")).strip()
                        else:
                            name = str(getattr(item, "name", "")).strip()
                            value = str(getattr(item, "value", "")).strip()
                        if name:
                            last_seen_names.add(name)
                        if name in ("sso", "sso-rw") and value:
                            if log_callback:
                                log_callback(f"[*] 已获取到 cookie {name}（CDP/tab）")
                            return value
                    if log_callback and time.time() - last_diag_at >= 5:
                        last_diag_at = time.time()
                        log_callback(
                            "[Debug] 等待 sso：页面 JS 暂不可用，继续轮询 cookie "
                            f"(seen={sorted(last_seen_names)})"
                        )
                    sleep_with_cancel(1, cancel_callback)
                    continue
            else:
                refresh_active_page(allow_restart=False)
            if page is None:
                sleep_with_cancel(1, cancel_callback)
                continue

            # Still on complete-signup page: Turnstile FIRST, then Complete — never reverse.
            # Space retries: first Complete often needs 10–20s for AuthManagement RPC;
            # hammering every 3s can abort in-flight signup XHR.
            now = time.time()
            # Grace period: only poll cookies / diagnose, do not re-click Complete.
            if now < first_complete_grace_until:
                cookies = _collect_browser_cookies()
                for item in cookies:
                    if isinstance(item, dict):
                        name = str(item.get("name", "")).strip()
                        value = str(item.get("value", "")).strip()
                    else:
                        name = str(getattr(item, "name", "")).strip()
                        value = str(getattr(item, "value", "")).strip()
                    if name:
                        last_seen_names.add(name)
                    if name in ("sso", "sso-rw") and value:
                        if log_callback:
                            log_callback(f"[*] 已获取到 cookie {name}")
                        return value
                if now - last_diag_at >= 8 and log_callback:
                    last_diag_at = now
                    if _page_is_alive():
                        diag = _final_page_diagnose()
                        log_callback(f"[Debug] 最终页诊断(grace): {diag}")
                        _diagnose_complete_button(log_callback=log_callback)
                    else:
                        log_callback(
                            f"[Debug] grace 轮询 cookie names={sorted(last_seen_names)}"
                        )
                sleep_with_cancel(1, cancel_callback)
                continue
            submit_gap = 15.0 if clicked_no_token_count == 0 else 10.0
            if now - last_submit_retry >= submit_gap:
                last_submit_retry = now
                # Periodic diagnostics while stuck
                if now - last_diag_at >= 12 and log_callback:
                    last_diag_at = now
                    diag = _final_page_diagnose()
                    log_callback(f"[Debug] 最终页诊断: {diag}")

                # 1) Always clear Turnstile before any Complete click
                state = _probe_turnstile_state() or {}
                token_ready = (
                    len(_read_turnstile_token()) >= 80
                    or len(_LAST_TURNSTILE_TOKEN) >= 80
                )
                needs_cf = (not token_ready) and (
                    _turnstile_needs_solve()
                    or bool(state.get("hasCfInput"))
                    or bool(state.get("visibleHuman"))
                    or int(state.get("widgets") or 0) > 0
                )
                if needs_cf:
                    if log_callback:
                        log_callback("[*] 最终页：Turnstile 未通过 — 只点验证框，不点 Complete")
                    try:
                        if now - last_cf_retry_at >= 2:
                            ensure_turnstile_before_submit(
                                log_callback=log_callback,
                                cancel_callback=cancel_callback,
                                timeout=28,
                                settle_seconds=2.0,
                            )
                            last_cf_retry_at = time.time()
                    except Exception as cf_exc:
                        if log_callback:
                            log_callback(f"[Debug] 最终页 Turnstile: {cf_exc}")
                    # Never Complete this loop if still unsolved
                    if len(_read_turnstile_token()) < 80 and _turnstile_needs_solve():
                        continue

                # 2) Only after token (or confirmed no challenge) click Complete
                # Re-inject CapMonster token if CF/SPA wiped the hidden input mid-wait.
                _ensure_turnstile_token_present(log_callback=log_callback)
                if _LAST_TURNSTILE_TOKEN:
                    _fire_turnstile_success_callbacks(
                        _LAST_TURNSTILE_TOKEN, log_callback=None
                    )
                tok_len_now = len(_read_turnstile_token() or _LAST_TURNSTILE_TOKEN)
                if log_callback:
                    log_callback(
                        f"[*] 最终页允许 Complete（token长度={tok_len_now}）"
                    )
                cdp_detail = _cdp_click_complete_signup(log_callback=log_callback)
                # Only one extra submit path per cycle (avoid triple-submit races).
                pw_detail = _playwright_click_signup_submit(log_callback=log_callback)
                form_detail = "skipped"
                if str(pw_detail) in ("no-pw", "pw-miss") and (
                    not cdp_detail or "no-btn" in str(cdp_detail)
                ):
                    form_detail = _form_request_submit_complete(log_callback=log_callback)

                retried = page.run_js(
                    r"""
function isVisible(node) {
    if (!node) return false;
    const style = window.getComputedStyle(node);
    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    const rect = node.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
}
const titleHit = !!Array.from(document.querySelectorAll('h1,h2,div,span,button')).find((el) => {
    const t = (el.textContent || '').replace(/\s+/g, '');
    const lower = t.toLowerCase();
    return (
      t.includes('完成注册') || t.includes('登録を完了') || t.includes('登録完了') ||
      t.includes('アカウント') || t.includes('Create') || t.includes('Sign up') ||
      lower.includes('completeyoursignup') || lower.includes('completesignup') ||
      lower.includes('createyouraccount') || lower.includes('finishsignup') ||
      lower.includes('createaccount')
    );
});
const hasProfile = !!(
  document.querySelector('input[name="givenName"], input[data-testid="givenName"], input[autocomplete="given-name"]') &&
  document.querySelector('input[type="password"], input[name="password"]')
);
if (!titleHit && !hasProfile) return 'not-final-page';

const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
const token = String((cfInput && cfInput.value) || '').trim();
const solved = token.length >= 80;
// Broader widget detection (blank src iframes + compact bars)
const cfWidgets = Array.from(document.querySelectorAll(
  'iframe[src*="turnstile"], iframe[src*="challenges.cloudflare.com"], div.cf-turnstile, [data-sitekey], iframe'
)).filter((f) => {
  if (f.tagName !== 'IFRAME') return true;
  const r = f.getBoundingClientRect();
  const src = String(f.src || '');
  if (/turnstile|challenges\.cloudflare|cf-chl/i.test(src)) return true;
  return r.width >= 200 && r.width <= 420 && r.height >= 40 && r.height <= 90;
}).length;
const body = (document.body && document.body.innerText || '').toLowerCase();
const visibleHuman = /sahkan anda manusia|verify you are human|confirm you are human|cloudflare|我是真人|人間/.test(body);
const realCfChallenge = cfWidgets > 0 || visibleHuman;

function buttonText(node) {
    return [
        node.innerText,
        node.textContent,
        node.getAttribute('value'),
        node.getAttribute('aria-label'),
        node.getAttribute('title'),
    ].filter(Boolean).join(' ').replace(/\s+/g, ' ').trim();
}
const buttons = Array.from(document.querySelectorAll('button[type="submit"], button, [role="button"], input[type="submit"]')).filter((node) => {
    return isVisible(node) && !node.disabled && node.getAttribute('aria-disabled') !== 'true';
});
const submitBtn = buttons.find((node) => {
    const t = buttonText(node).replace(/\s+/g, '').toLowerCase();
    if (t.includes('email') && !t.includes('complete')) return false;
    if (t.includes('goback') || t.includes('go back') || t.includes('返回') || t.includes('戻る')) return false;
    return (
      t.includes('完成注册') || t.includes('创建账户') || t.includes('创建帐户') ||
      t.includes('completesignup') || t.includes('completeyoursignup') ||
      t.includes('createaccount') || t.includes('complete sign up') ||
      t.includes('登録を完了') || t.includes('登録完了') || t.includes('アカウントを作成') ||
      t.includes('登録する') ||
      (t.includes('sign up') && !t.includes('with')) ||
      t.includes('submit') || t.includes('continue') || t.includes('next')
    );
});

if (realCfChallenge && !solved) {
    return 'final-page-wait-cf:' + token.length + ':widgets=' + cfWidgets + (visibleHuman ? ':human' : '');
}

if (!submitBtn) {
    const visibleTexts = buttons.map(buttonText).filter(Boolean).slice(0, 8).join(' | ');
    return 'final-page-no-submit:' + visibleTexts;
}

submitBtn.setAttribute('data-grok-complete-signup', '1');
submitBtn.focus();
return 'final-page-marked-submit' + (solved ? ':token' : ':no-token') + (realCfChallenge ? ':widget' : ':no-widget') + ':btn=' + buttonText(submitBtn).slice(0, 40);
                    """
                )
                if isinstance(retried, str) and "no-token" in retried and "wait-cf" not in retried:
                    clicked_no_token_count += 1
                if isinstance(retried, str) and retried.startswith("final-page-wait-cf"):
                    if log_callback:
                        log_callback(f"[Debug] 最终页仍需 Turnstile: {retried}")
                    try:
                        ensure_turnstile_before_submit(
                            log_callback=log_callback,
                            cancel_callback=cancel_callback,
                            timeout=25,
                        )
                    except Exception as cf_exc:
                        if log_callback:
                            log_callback(f"[Debug] 最终页 Turnstile: {cf_exc}")
                    continue

                if log_callback and (
                    (isinstance(retried, str) and retried.startswith("final-page-"))
                    or pw_detail not in ("no-pw", "pw-miss")
                    or (isinstance(cdp_detail, str) and "cdp-complete" in str(cdp_detail))
                ):
                    log_callback(
                        f"[Debug] 最终页状态: {retried} | cdp={cdp_detail} | "
                        f"pw={pw_detail} | form={form_detail}"
                    )

                # After many no-token submits, surface diagnosis and retry account
                if clicked_no_token_count >= 6:
                    diag = _final_page_diagnose()
                    _log_network_failures(log_callback)
                    if log_callback:
                        log_callback(f"[!] 多次提交仍无 sso，诊断={diag}")
                    raise AccountRetryNeeded(
                        f"最终页多次提交无 sso（可能被服务端拒绝/需真人验证）: {diag.get('errors') or diag.get('bodySnippet') or retried}"
                    )
                if clicked_no_token_count in (2, 4) and log_callback:
                    _log_network_failures(log_callback)

                if isinstance(retried, str) and retried.startswith("final-page-no-submit"):
                    if retried != final_no_submit_state:
                        final_no_submit_state = retried
                        final_no_submit_since = now
                    elif final_no_submit_since and now - final_no_submit_since >= final_no_submit_timeout:
                        raise AccountRetryNeeded(
                            f"最终注册页状态 {final_no_submit_timeout}s 未变化且未找到提交按钮，重试当前账号: {retried}"
                        )
                else:
                    final_no_submit_state = ""
                    final_no_submit_since = None

            cookies = _collect_browser_cookies()
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()

                if name:
                    last_seen_names.add(name)

                if name in ("sso", "sso-rw") and value:
                    if log_callback:
                        log_callback(f"[*] 已获取到 cookie {name}")
                    return value
        except PageDisconnectedError:
            # Soft reattach; do NOT restart (would wipe cookies mid-signup success).
            refresh_active_page(allow_restart=False)
            cookies = _collect_browser_cookies()
            for item in cookies:
                if isinstance(item, dict):
                    name = str(item.get("name", "")).strip()
                    value = str(item.get("value", "")).strip()
                else:
                    name = str(getattr(item, "name", "")).strip()
                    value = str(getattr(item, "value", "")).strip()
                if name:
                    last_seen_names.add(name)
                if name in ("sso", "sso-rw") and value:
                    if log_callback:
                        log_callback(f"[*] 已获取到 cookie {name}（PageDisconnected 后）")
                    return value
        except AccountRetryNeeded:
            raise
        except RegistrationCancelled:
            raise
        except Exception as exc:
            err = str(exc)
            # Soft recovery: keep polling cookies instead of killing the Roxy profile
            if "closed" in err.lower() or "Target page" in err or "disconnected" in err.lower():
                refresh_active_page(allow_restart=False)
                cookies = _collect_browser_cookies()
                for item in cookies:
                    if isinstance(item, dict):
                        name = str(item.get("name", "")).strip()
                        value = str(item.get("value", "")).strip()
                    else:
                        name = str(getattr(item, "name", "")).strip()
                        value = str(getattr(item, "value", "")).strip()
                    if name:
                        last_seen_names.add(name)
                    if name in ("sso", "sso-rw") and value:
                        if log_callback:
                            log_callback(f"[*] 已获取到 cookie {name}（异常后）")
                        return value
                if log_callback:
                    log_callback(f"[Debug] 等待 sso 页面异常（继续）: {err[:120]}")
            elif log_callback:
                log_callback(f"[Debug] 等待 sso 异常: {err[:120]}")

        sleep_with_cancel(1, cancel_callback)

    diag = _final_page_diagnose()
    raise Exception(
        f"等待超时：未获取到 sso cookie。已看到 cookies: {sorted(last_seen_names)}; 最终诊断={diag}"
    )


class GrokRegisterGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Grok 注册机")
        self.root.geometry("1120x900")
        self.root.minsize(960, 700)
        self.is_running = False
        self.batch_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        self.stop_requested = False
        self.ui_queue = queue.Queue()
        self.accounts_output_file = ""
        self.setup_ui()

    def setup_ui(self):
        load_config()
        main_frame = tk.Frame(self.root, bg=UI_BG, padx=10, pady=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        main_frame.grid_columnconfigure(0, weight=1)
        main_frame.grid_rowconfigure(3, weight=1)

        config_frame = tk.LabelFrame(
            main_frame,
            text="配置",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=10,
            pady=10,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        config_frame.grid(row=0, column=0, sticky=tk.EW, pady=(0, 8))
        config_frame.grid_columnconfigure(1, weight=1, minsize=260)
        config_frame.grid_columnconfigure(3, weight=1, minsize=260)

        def add_label(row, column, text):
            tk_label(config_frame, text=text, bg=UI_PANEL_BG).grid(
                row=row,
                column=column,
                sticky=tk.W,
                padx=(0, 6),
                pady=3,
            )

        def add_field(widget, row, column, columnspan=1, sticky=tk.EW):
            widget.grid(
                row=row,
                column=column,
                columnspan=columnspan,
                sticky=sticky,
                padx=(0, 14),
                pady=3,
            )

        add_label(0, 0, "邮箱服务商:")
        self.email_provider_var = tk.StringVar(value=config.get("email_provider", "duckmail"))
        self.email_provider_combo = tk_option_menu(config_frame, self.email_provider_var, ["duckmail", "yyds", "cloudflare"], width=12)
        add_field(self.email_provider_combo, 0, 1, sticky=tk.W)

        add_label(0, 2, "注册数量:")
        self.count_var = tk.StringVar(value=str(config.get("register_count", 1)))
        self.count_spinbox = tk.Spinbox(
            config_frame,
            from_=1,
            to=2500,
            width=8,
            textvariable=self.count_var,
            bg=UI_ENTRY_BG,
            fg=UI_FG,
            insertbackground=UI_FG,
            buttonbackground=UI_BUTTON_BG,
            disabledbackground="#2f2f2f",
            disabledforeground=UI_MUTED_FG,
            relief=tk.SOLID,
        )
        add_field(self.count_spinbox, 0, 3, sticky=tk.W)

        add_label(1, 0, "注册选项:")
        self.nsfw_var = tk.BooleanVar(value=config.get("enable_nsfw", True))
        self.nsfw_check = tk_checkbutton(config_frame, text="注册后开启 NSFW", variable=self.nsfw_var)
        add_field(self.nsfw_check, 1, 1, sticky=tk.W)

        add_label(1, 2, "本地代理（可选）:")
        self.proxy_var = tk.StringVar(value=config.get("proxy", ""))
        self.proxy_entry = tk_entry(config_frame, textvariable=self.proxy_var, width=34)
        add_field(self.proxy_entry, 1, 3)

        add_label(2, 0, "浏览器驱动:")
        driver_default = str(config.get("browser_driver", "local") or "local")
        low = driver_default.lower()
        if low in ("browser_use", "browseruse", "browser-use", "bu", "cloud"):
            driver_default = "browser_use"
        elif low in ("roxy", "roxybrowser", "roxy_browser", "fingerprint"):
            driver_default = "roxy"
        else:
            driver_default = "local"
        self.browser_driver_var = tk.StringVar(value=driver_default)
        self.browser_driver_combo = tk_option_menu(
            config_frame, self.browser_driver_var, ["local", "browser_use", "roxy"], width=12
        )
        add_field(self.browser_driver_combo, 2, 1, sticky=tk.W)

        add_label(2, 2, "BU 国家代码:")
        self.browser_use_country_var = tk.StringVar(
            value=str(config.get("browser_use_proxy_country", "us") or "us")
        )
        self.browser_use_country_entry = tk_entry(
            config_frame, textvariable=self.browser_use_country_var, width=12
        )
        add_field(self.browser_use_country_entry, 2, 3, sticky=tk.W)

        add_label(3, 0, "Browser Use API Key:")
        self.browser_use_api_key_var = tk.StringVar(
            value=str(config.get("browser_use_api_key", "") or "")
        )
        self.browser_use_api_key_entry = tk_entry(
            config_frame, textvariable=self.browser_use_api_key_var, width=34
        )
        add_field(self.browser_use_api_key_entry, 3, 1)

        add_label(3, 2, "BU 选项:")
        self.browser_use_use_proxy_var = tk.BooleanVar(
            value=bool(config.get("browser_use_use_proxy", True))
        )
        self.browser_use_use_proxy_check = tk_checkbutton(
            config_frame, text="使用内置代理", variable=self.browser_use_use_proxy_var
        )
        add_field(self.browser_use_use_proxy_check, 3, 3, sticky=tk.W)

        add_label(4, 0, "BU Profile ID:")
        self.browser_use_profile_id_var = tk.StringVar(
            value=str(config.get("browser_use_profile_id", "") or "")
        )
        self.browser_use_profile_id_entry = tk_entry(
            config_frame, textvariable=self.browser_use_profile_id_var, width=34
        )
        add_field(self.browser_use_profile_id_entry, 4, 1)

        add_label(4, 2, "BU 会话分钟:")
        self.browser_use_timeout_var = tk.StringVar(
            value=str(config.get("browser_use_timeout_minutes", 15) or 15)
        )
        self.browser_use_timeout_entry = tk_entry(
            config_frame, textvariable=self.browser_use_timeout_var, width=12
        )
        add_field(self.browser_use_timeout_entry, 4, 3, sticky=tk.W)

        add_label(5, 0, "Roxy API Base:")
        self.roxy_api_base_var = tk.StringVar(
            value=str(config.get("roxy_api_base", "http://127.0.0.1:50000") or "http://127.0.0.1:50000")
        )
        self.roxy_api_base_entry = tk_entry(
            config_frame, textvariable=self.roxy_api_base_var, width=34
        )
        add_field(self.roxy_api_base_entry, 5, 1)

        add_label(5, 2, "Roxy Token:")
        self.roxy_api_token_var = tk.StringVar(
            value=str(config.get("roxy_api_token", "") or "")
        )
        self.roxy_api_token_entry = tk_entry(
            config_frame, textvariable=self.roxy_api_token_var, width=34
        )
        add_field(self.roxy_api_token_entry, 5, 3)

        add_label(6, 0, "Roxy Workspace ID:")
        self.roxy_workspace_id_var = tk.StringVar(
            value=str(config.get("roxy_workspace_id", "") or "")
        )
        self.roxy_workspace_id_entry = tk_entry(
            config_frame, textvariable=self.roxy_workspace_id_var, width=34
        )
        add_field(self.roxy_workspace_id_entry, 6, 1)

        add_label(6, 2, "Roxy 选项:")
        self.roxy_one_profile_var = tk.BooleanVar(
            value=bool(config.get("roxy_one_profile_per_account", True))
        )
        self.roxy_one_profile_check = tk_checkbutton(
            config_frame, text="一号一环境(创建/删除)", variable=self.roxy_one_profile_var
        )
        add_field(self.roxy_one_profile_check, 6, 3, sticky=tk.W)

        add_label(7, 0, "CapMonster API Key:")
        self.capmonster_api_key_var = tk.StringVar(
            value=str(config.get("capmonster_api_key", "") or "")
        )
        self.capmonster_api_key_entry = tk_entry(
            config_frame, textvariable=self.capmonster_api_key_var, width=34
        )
        add_field(self.capmonster_api_key_entry, 7, 1)

        add_label(7, 2, "CapMonster:")
        self.capmonster_enabled_var = tk.BooleanVar(
            value=bool(config.get("capmonster_enabled", True))
        )
        self.capmonster_enabled_check = tk_checkbutton(
            config_frame, text="云解 Turnstile", variable=self.capmonster_enabled_var
        )
        add_field(self.capmonster_enabled_check, 7, 3, sticky=tk.W)

        add_label(8, 0, "DuckMail API Key:")
        self.api_key_var = tk.StringVar(value=config.get("duckmail_api_key", ""))
        self.api_key_entry = tk_entry(config_frame, textvariable=self.api_key_var, width=34)
        add_field(self.api_key_entry, 8, 1)

        add_label(8, 2, "Cloudflare 鉴权模式:")
        self.cloudflare_auth_mode_var = tk.StringVar(value=config.get("cloudflare_auth_mode", "none"))
        self.cloudflare_auth_mode_combo = tk_option_menu(
            config_frame, self.cloudflare_auth_mode_var, ["query-key", "bearer", "x-api-key", "x-admin-auth", "none"], width=12
        )
        add_field(self.cloudflare_auth_mode_combo, 8, 3, sticky=tk.W)

        add_label(9, 0, "Cloudflare API Base:")
        self.cloudflare_api_base_var = tk.StringVar(value=config.get("cloudflare_api_base", ""))
        self.cloudflare_api_base_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_base_var, width=72)
        add_field(self.cloudflare_api_base_entry, 9, 1, columnspan=3)

        add_label(10, 0, "Cloudflare API Key:")
        self.cloudflare_api_key_var = tk.StringVar(value=config.get("cloudflare_api_key", ""))
        self.cloudflare_api_key_entry = tk_entry(config_frame, textvariable=self.cloudflare_api_key_var, width=34)
        add_field(self.cloudflare_api_key_entry, 10, 1)

        add_label(10, 2, "CF 路径:")
        self.cloudflare_paths_var = tk.StringVar(
            value=",".join(
                [
                    config.get("cloudflare_path_domains", "/api/domains"),
                    config.get("cloudflare_path_accounts", "/api/new_address"),
                    config.get("cloudflare_path_token", "/api/token"),
                    config.get("cloudflare_path_messages", "/api/mails"),
                ]
            )
        )
        self.cloudflare_paths_entry = tk_entry(config_frame, textvariable=self.cloudflare_paths_var, width=34)
        add_field(self.cloudflare_paths_entry, 10, 3)

        add_label(11, 0, "Cloudflare 收信域名:")
        self.default_domains_var = tk.StringVar(value=str(config.get("defaultDomains", "")))
        self.default_domains_entry = tk_entry(config_frame, textvariable=self.default_domains_var, width=34)
        add_field(self.default_domains_entry, 11, 1)

        add_label(11, 2, "Cloudflare 全局密码:")
        self.cloudflare_custom_auth_var = tk.StringVar(value=str(config.get("cloudflare_custom_auth", "")))
        self.cloudflare_custom_auth_entry = tk_entry(config_frame, textvariable=self.cloudflare_custom_auth_var, width=34)
        add_field(self.cloudflare_custom_auth_entry, 11, 3)

        add_label(12, 0, "CPA 直出(SSO→auth):")
        self.cpa_auto_add_var = tk.BooleanVar(value=bool(config.get("cpa_auto_add", False)))
        self.cpa_auto_add_check = tk_checkbutton(config_frame, variable=self.cpa_auto_add_var)
        add_field(self.cpa_auto_add_check, 12, 1, sticky=tk.W)

        add_label(13, 0, "CPA auth 目录:")
        self.cpa_auth_dir_var = tk.StringVar(value=str(config.get("cpa_auth_dir", "")))
        self.cpa_auth_dir_entry = tk_entry(config_frame, textvariable=self.cpa_auth_dir_var, width=72)
        add_field(self.cpa_auth_dir_entry, 13, 1, columnspan=3)

        add_label(14, 0, "CPA 远程地址:")
        self.cpa_remote_url_var = tk.StringVar(value=str(config.get("cpa_remote_url", "")))
        self.cpa_remote_url_entry = tk_entry(config_frame, textvariable=self.cpa_remote_url_var, width=40)
        add_field(self.cpa_remote_url_entry, 14, 1)

        add_label(14, 2, "CPA 管理密钥:")
        self.cpa_management_key_var = tk.StringVar(value=str(config.get("cpa_management_key", "")))
        self.cpa_management_key_entry = tk_entry(config_frame, textvariable=self.cpa_management_key_var, width=28)
        add_field(self.cpa_management_key_entry, 14, 3)

        btn_frame = tk.Frame(main_frame, bg=UI_BG)
        btn_frame.grid(row=1, column=0, sticky=tk.EW, pady=(0, 6))
        self.start_btn = tk_button(btn_frame, text="开始注册", command=self.start_registration)
        self.start_btn.pack(side=tk.LEFT, padx=5)
        self.stop_btn = tk_button(btn_frame, text="停止", command=self.stop_registration, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=5)
        self.clear_btn = tk_button(btn_frame, text="清空日志", command=self.clear_log)
        self.clear_btn.pack(side=tk.LEFT, padx=5)

        status_frame = tk.Frame(main_frame, bg=UI_BG)
        status_frame.grid(row=2, column=0, sticky=tk.EW, pady=(0, 6))
        self.status_var = tk.StringVar(value="就绪")
        tk_label(status_frame, text="状态: ").pack(side=tk.LEFT)
        self.status_label = tk.Label(status_frame, textvariable=self.status_var, bg=UI_BG, fg="green")
        self.status_label.pack(side=tk.LEFT)
        self.stats_var = tk.StringVar(value="成功: 0 | 失败: 0")
        tk.Label(status_frame, textvariable=self.stats_var, bg=UI_BG, fg=UI_FG).pack(side=tk.RIGHT)
        log_frame = tk.LabelFrame(
            main_frame,
            text="日志",
            bg=UI_PANEL_BG,
            fg=UI_FG,
            padx=5,
            pady=5,
            relief=tk.GROOVE,
            borderwidth=1,
        )
        log_frame.grid(row=3, column=0, sticky=tk.NSEW)
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)
        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=18,
            width=60,
            bg="#111111",
            fg="#f5f5f5",
            insertbackground="#f5f5f5",
            selectbackground="#345a8a",
            selectforeground="#ffffff",
            relief=tk.SOLID,
            borderwidth=1,
            highlightthickness=1,
            highlightbackground="#555555",
        )
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)
        self.log("[*] GUI 已就绪，配置已加载")
        self.log(
            f"[*] 邮箱服务商: {self.email_provider_var.get()} | 驱动: {self.browser_driver_var.get()} "
            f"| 数量: {self.count_var.get()}"
        )

    def log(self, message):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}"
        print(line, flush=True)
        self.log_text.insert(tk.END, f"{line}\n")
        self.log_text.see(tk.END)

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def update_stats(self):
        self.stats_var.set(f"成功: {self.success_count} | 失败: {self.fail_count}")

    def _set_running_ui(self, running):
        self.is_running = running
        self.start_btn.config(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.config(state=tk.NORMAL if running else tk.DISABLED)
        self.status_var.set("运行中..." if running else "就绪")
        self.status_label.config(foreground="blue" if running else "green")

    def should_stop(self):
        return self.stop_requested or not self.is_running

    def start_registration(self):
        if self.is_running:
            self.log("[!] 当前已有任务在运行")
            return

        config["email_provider"] = self.email_provider_var.get().strip() or "duckmail"
        config["enable_nsfw"] = bool(self.nsfw_var.get())
        config["proxy"] = self.proxy_var.get().strip()
        config["browser_driver"] = self.browser_driver_var.get().strip() or "local"
        config["browser_use_api_key"] = self.browser_use_api_key_var.get().strip()
        config["browser_use_proxy_country"] = self.browser_use_country_var.get().strip().lower()
        config["browser_use_use_proxy"] = bool(self.browser_use_use_proxy_var.get())
        config["browser_use_profile_id"] = self.browser_use_profile_id_var.get().strip()
        try:
            config["browser_use_timeout_minutes"] = int(
                str(self.browser_use_timeout_var.get() or "15").strip() or "15"
            )
        except Exception:
            config["browser_use_timeout_minutes"] = 15
        config["roxy_api_base"] = self.roxy_api_base_var.get().strip() or "http://127.0.0.1:50000"
        config["roxy_api_token"] = self.roxy_api_token_var.get().strip()
        config["roxy_workspace_id"] = self.roxy_workspace_id_var.get().strip()
        config["roxy_one_profile_per_account"] = bool(self.roxy_one_profile_var.get())
        config["capmonster_api_key"] = self.capmonster_api_key_var.get().strip()
        config["capmonster_enabled"] = bool(self.capmonster_enabled_var.get())
        config["duckmail_api_key"] = self.api_key_var.get().strip()
        config["cloudflare_api_base"] = self.cloudflare_api_base_var.get().strip()
        config["cloudflare_api_key"] = self.cloudflare_api_key_var.get().strip()
        config["cloudflare_auth_mode"] = self.cloudflare_auth_mode_var.get().strip() or "none"
        config["defaultDomains"] = self.default_domains_var.get().strip()
        config["cloudflare_custom_auth"] = self.cloudflare_custom_auth_var.get().strip()
        config["cpa_auto_add"] = bool(self.cpa_auto_add_var.get())
        config["cpa_auth_dir"] = self.cpa_auth_dir_var.get().strip()
        config["cpa_remote_url"] = self.cpa_remote_url_var.get().strip()
        config["cpa_management_key"] = self.cpa_management_key_var.get().strip()
        raw_paths = [x.strip() for x in self.cloudflare_paths_var.get().split(",") if x.strip()]
        if len(raw_paths) >= 4:
            config["cloudflare_path_domains"] = raw_paths[0] if raw_paths[0].startswith("/") else ("/" + raw_paths[0])
            config["cloudflare_path_accounts"] = raw_paths[1] if raw_paths[1].startswith("/") else ("/" + raw_paths[1])
            config["cloudflare_path_token"] = raw_paths[2] if raw_paths[2].startswith("/") else ("/" + raw_paths[2])
            config["cloudflare_path_messages"] = raw_paths[3] if raw_paths[3].startswith("/") else ("/" + raw_paths[3])
        save_config()
        if config["email_provider"] == "cloudflare" and not config["cloudflare_api_base"]:
            self.log("[!] Cloudflare 模式需要先填写 Cloudflare API Base")
            return
        if get_browser_driver() == "browser_use" and not str(config.get("browser_use_api_key") or "").strip():
            self.log("[!] Browser Use 模式需要填写 browser_use_api_key")
            return
        if get_browser_driver() == "roxy" and not str(config.get("roxy_api_token") or "").strip():
            self.log("[!] Roxy 模式需要填写 roxy_api_token（config / 环境变量 ROXY_API_TOKEN）")
            return
        try:
            count = int(self.count_var.get())
        except Exception:
            self.log("[!] 注册数量无效")
            return
        config["register_count"] = count
        save_config()
        self.stop_requested = False
        self.success_count = 0
        self.fail_count = 0
        self.results = []
        now = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        self.accounts_output_file = os.path.join(
            os.path.dirname(__file__), f"accounts_{now}.txt"
        )
        self.update_stats()
        self._set_running_ui(True)
        self.log(f"[*] 配置已保存，开始执行。目标数量: {count}")
        self.log(f"[*] 浏览器驱动: {get_browser_driver()}")
        if get_browser_driver() == "browser_use":
            self.log(
                f"[*] Browser Use: country={config.get('browser_use_proxy_country') or '-'} "
                f"use_proxy={config.get('browser_use_use_proxy')} "
                f"timeout_min={config.get('browser_use_timeout_minutes')}"
            )
        if get_browser_driver() == "roxy":
            self.log(
                f"[*] Roxy: api={config.get('roxy_api_base')} "
                f"workspace={config.get('roxy_workspace_id') or 'auto'} "
                f"one_profile={config.get('roxy_one_profile_per_account')} "
                f"delete_after={config.get('roxy_delete_profile_after_run', True)}"
            )
        self.log(f"[*] 成功账号将实时保存到: {self.accounts_output_file}")
        threading.Thread(
            target=self.run_registration,
            args=(count,),
            daemon=True,
        ).start()

    def stop_registration(self):
        self.stop_requested = True
        self.log("[!] 用户停止注册")

    def run_registration(self, count):
        try:
            start_browser(log_callback=self.log)
            self.log("[*] 浏览器已启动")
            i = 0
            retry_count_for_slot = 0
            max_slot_retry = 3
            while i < count:
                if self.should_stop():
                    break
                self.log(f"--- 开始第 {i + 1}/{count} 个账号 ---")
                try:
                    email = ""
                    dev_token = ""
                    code = ""
                    mail_ok = False
                    max_mail_retry = 3
                    for mail_try in range(1, max_mail_retry + 1):
                        self.log(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
                        open_signup_page(
                            log_callback=self.log, cancel_callback=self.should_stop
                        )
                        self.log("[*] 2. 创建邮箱并提交")
                        email, dev_token = fill_email_and_submit(
                            log_callback=self.log, cancel_callback=self.should_stop
                        )
                        self.log(f"[*] 邮箱: {email}")
                        self.log(f"[Debug] 邮箱credential(jwt): {dev_token}")
                        try:
                            with open(
                                os.path.join(os.path.dirname(__file__), "mail_credentials.txt"),
                                "a",
                                encoding="utf-8",
                            ) as f:
                                f.write(f"{email}\t{dev_token}\n")
                        except Exception:
                            pass
                        self.log("[*] 3. 拉取验证码")
                        try:
                            code = fill_code_and_submit(
                                email,
                                dev_token,
                                log_callback=self.log,
                                cancel_callback=self.should_stop,
                            )
                            mail_ok = True
                            break
                        except Exception as mail_exc:
                            msg = str(mail_exc)
                            if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                                self.log(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                                restart_browser(log_callback=self.log)
                                sleep_with_cancel(1, self.should_stop)
                                continue
                            raise

                    if not mail_ok:
                        raise Exception("验证码阶段失败，已达到最大重试次数")
                    self.log(f"[*] 验证码: {code}")
                    self.log("[*] 4. 填写资料")
                    profile = fill_profile_and_submit(
                        log_callback=self.log, cancel_callback=self.should_stop
                    )
                    self.log(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
                    self.log("[*] 5. 等待 sso cookie")
                    sso = wait_for_sso_cookie(
                        log_callback=self.log, cancel_callback=self.should_stop
                    )
                    if config.get("enable_nsfw", True):
                        self.log("[*] 6. 开启 NSFW")
                        cf_clearance, browser_ua = extract_cf_clearance_and_ua(self.log)
                        nsfw_ok, nsfw_msg = enable_nsfw_for_token(
                            sso, cf_clearance=cf_clearance, user_agent=browser_ua, log_callback=self.log
                        )
                        if nsfw_ok:
                            self.log(f"[+] NSFW 开启成功: {nsfw_msg}")
                        else:
                            self.log(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
                    self.results.append({"email": email, "sso": sso, "profile": profile})
                    try:
                        line = f"{email}----{profile.get('password','')}----{sso}\n"
                        with open(self.accounts_output_file, "a", encoding="utf-8") as f:
                            f.write(line)
                    except Exception as file_exc:
                        self.log(f"[Debug] 保存账号文件失败: {file_exc}")
                    add_sso_to_cpa(sso, email=email, log_callback=self.log)
                    self.success_count += 1
                    retry_count_for_slot = 0
                    i += 1
                    self.log(f"[+] 注册成功: {email}")
                    if (
                        self.success_count > 0
                        and self.success_count % MEMORY_CLEANUP_INTERVAL == 0
                        and i < count
                    ):
                        cleanup_runtime_memory(
                            log_callback=self.log,
                            reason=f"已成功 {self.success_count} 个账号，执行定期清理",
                        )
                except RegistrationCancelled:
                    self.log("[!] 注册被用户停止")
                    break
                except AccountRetryNeeded as exc:
                    retry_count_for_slot += 1
                    if retry_count_for_slot <= max_slot_retry:
                        self.log(
                            f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                        )
                    else:
                        self.fail_count += 1
                        self.log(
                            f"[-] 当前账号已达到最大重试次数，跳过: {exc}"
                        )
                        retry_count_for_slot = 0
                        i += 1
                except Exception as exc:
                    self.fail_count += 1
                    retry_count_for_slot = 0
                    i += 1
                    self.log(f"[-] 注册失败: {exc}")
                finally:
                    self.update_stats()
                    if self.should_stop():
                        break
                    try:
                        if browser is None:
                            start_browser(log_callback=self.log)
                        else:
                            restart_browser(log_callback=self.log)
                        # 停止后不再调用 cancel_callback，避免 finally 里二次抛出 RegistrationCancelled
                        time.sleep(1)
                    except RegistrationCancelled:
                        break
                    except Exception as restart_exc:
                        if self.should_stop():
                            break
                        self.log(f"[Debug] 轮次清理/重启浏览器失败: {restart_exc}")
        except RegistrationCancelled:
            self.log("[!] 注册被用户停止")
        except Exception as exc:
            self.log(f"[!] 任务异常: {exc}")
        finally:
            try:
                stop_browser()
            except BaseException:
                pass
            self._set_running_ui(False)
            self.log("[*] 任务结束")


class CliStopController:
    def __init__(self):
        self.stop_requested = False

    def should_stop(self):
        return self.stop_requested

    def stop(self):
        self.stop_requested = True


def cli_log(message):
    timestamp = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def run_registration_cli(count):
    controller = CliStopController()

    # 一次 Ctrl+C 可靠置停：SIGINT 处理器直接设停止标志，不依赖异常在
    # curl_cffi C 回调里向上传播（那里 KeyboardInterrupt 会被吞掉，导致
    # 第一次 Ctrl+C 无效、循环继续跑下一个账号）。连按两次 Ctrl+C 时第二次
    # 恢复默认行为强制中断。
    _prev_sigint = signal.getsignal(signal.SIGINT)

    def _on_sigint(signum, frame):
        if controller.should_stop():
            # 第二次：恢复默认并重新抛出，强制中断
            signal.signal(signal.SIGINT, _prev_sigint)
            raise KeyboardInterrupt
        controller.stop()
        cli_log("[!] 收到 Ctrl+C，正在停止（再按一次强制中断）")

    signal.signal(signal.SIGINT, _on_sigint)
    success_count = 0
    fail_count = 0
    retry_count_for_slot = 0
    max_slot_retry = 3
    accounts_output_file = os.path.join(
        os.path.dirname(__file__),
        f"accounts_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
    )
    cli_log(f"[*] 终端模式启动，目标数量: {count}")
    cli_log(f"[*] 成功账号将实时保存到: {accounts_output_file}")
    try:
        start_browser(log_callback=cli_log)
        cli_log("[*] 浏览器已启动")
        i = 0
        while i < count:
            if controller.should_stop():
                break
            cli_log(f"--- 开始第 {i + 1}/{count} 个账号 ---")
            try:
                email = ""
                dev_token = ""
                code = ""
                mail_ok = False
                max_mail_retry = 3
                for mail_try in range(1, max_mail_retry + 1):
                    cli_log(f"[*] 1. 打开注册页 (尝试 {mail_try}/{max_mail_retry})")
                    open_signup_page(
                        log_callback=cli_log, cancel_callback=controller.should_stop
                    )
                    cli_log("[*] 2. 创建邮箱并提交")
                    email, dev_token = fill_email_and_submit(
                        log_callback=cli_log, cancel_callback=controller.should_stop
                    )
                    cli_log(f"[*] 邮箱: {email}")
                    cli_log(f"[Debug] 邮箱credential(jwt): {dev_token}")
                    try:
                        with open(
                            os.path.join(os.path.dirname(__file__), "mail_credentials.txt"),
                            "a",
                            encoding="utf-8",
                        ) as f:
                            f.write(f"{email}\t{dev_token}\n")
                    except Exception:
                        pass
                    cli_log("[*] 3. 拉取验证码")
                    try:
                        code = fill_code_and_submit(
                            email,
                            dev_token,
                            log_callback=cli_log,
                            cancel_callback=controller.should_stop,
                        )
                        mail_ok = True
                        break
                    except Exception as mail_exc:
                        msg = str(mail_exc)
                        if ("未收到验证码" in msg or "验证码" in msg) and mail_try < max_mail_retry:
                            cli_log(f"[!] 本邮箱未取到验证码，自动更换新邮箱重试: {msg}")
                            restart_browser(log_callback=cli_log)
                            sleep_with_cancel(1, controller.should_stop)
                            continue
                        raise

                if not mail_ok:
                    raise Exception("验证码阶段失败，已达到最大重试次数")
                cli_log(f"[*] 验证码: {code}")
                cli_log("[*] 4. 填写资料")
                profile = fill_profile_and_submit(
                    log_callback=cli_log, cancel_callback=controller.should_stop
                )
                cli_log(f"[*] 资料已填: {profile.get('given_name')} {profile.get('family_name')}")
                cli_log("[*] 5. 等待 sso cookie")
                sso = wait_for_sso_cookie(
                    log_callback=cli_log, cancel_callback=controller.should_stop
                )
                if config.get("enable_nsfw", True):
                    cli_log("[*] 6. 开启 NSFW")
                    cf_clearance, browser_ua = extract_cf_clearance_and_ua(log_callback=cli_log)
                    nsfw_ok, nsfw_msg = enable_nsfw_for_token(
                        sso, cf_clearance=cf_clearance, user_agent=browser_ua, log_callback=cli_log
                    )
                    if nsfw_ok:
                        cli_log(f"[+] NSFW 开启成功: {nsfw_msg}")
                    else:
                        cli_log(f"[!] NSFW 未开启，继续保存账号: {nsfw_msg}")
                try:
                    line = f"{email}----{profile.get('password','')}----{sso}\n"
                    with open(accounts_output_file, "a", encoding="utf-8") as f:
                        f.write(line)
                except Exception as file_exc:
                    cli_log(f"[Debug] 保存账号文件失败: {file_exc}")
                add_sso_to_cpa(sso, email=email, log_callback=cli_log)
                success_count += 1
                retry_count_for_slot = 0
                i += 1
                cli_log(f"[+] 注册成功: {email}")
                cli_log(f"[*] 当前统计: 成功 {success_count} | 失败 {fail_count}")
                if success_count > 0 and success_count % MEMORY_CLEANUP_INTERVAL == 0 and i < count:
                    cleanup_runtime_memory(
                        log_callback=cli_log,
                        reason=f"已成功 {success_count} 个账号，执行定期清理",
                    )
            except RegistrationCancelled:
                cli_log("[!] 注册被停止")
                break
            except AccountRetryNeeded as exc:
                retry_count_for_slot += 1
                if retry_count_for_slot <= max_slot_retry:
                    cli_log(
                        f"[!] 当前账号流程卡住，重试第 {retry_count_for_slot}/{max_slot_retry} 次: {exc}"
                    )
                else:
                    fail_count += 1
                    retry_count_for_slot = 0
                    i += 1
                    cli_log(f"[-] 当前账号已达到最大重试次数，跳过: {exc}")
            except Exception as exc:
                fail_count += 1
                retry_count_for_slot = 0
                i += 1
                cli_log(f"[-] 注册失败: {exc}")
            finally:
                if controller.should_stop():
                    break
                try:
                    if browser is None:
                        start_browser(log_callback=cli_log)
                    else:
                        restart_browser(log_callback=cli_log)
                    # 停止后不再调用 cancel_callback，避免 finally 里二次抛出 RegistrationCancelled
                    time.sleep(1)
                except KeyboardInterrupt:
                    controller.stop()
                    cli_log("[!] 收到 Ctrl+C，正在停止（再按一次强制中断）")
                    break
                except RegistrationCancelled:
                    break
                except Exception as restart_exc:
                    if controller.should_stop():
                        break
                    cli_log(f"[Debug] 轮次清理/重启浏览器失败: {restart_exc}")
    except KeyboardInterrupt:
        controller.stop()
        cli_log("[!] 收到 Ctrl+C，正在停止并清理")
    except RegistrationCancelled:
        cli_log("[!] 注册被停止")
    except Exception as exc:
        cli_log(f"[!] 任务异常: {exc}")
    finally:
        try:
            signal.signal(signal.SIGINT, signal.SIG_IGN)
        except Exception:
            pass
        try:
            cleanup_runtime_memory(log_callback=cli_log, reason="任务结束")
        except BaseException:
            pass
        try:
            cli_log(f"[*] 任务结束。成功 {success_count} | 失败 {fail_count}")
        except BaseException:
            pass
        try:
            signal.signal(signal.SIGINT, _prev_sigint)
        except Exception:
            pass


def _apply_cli_overrides(argv):
    """Parse optional CLI flags into config. Returns remaining positional args."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="grok_register_ttk.py",
        description="Grok 注册机 CLI / GUI",
        add_help=True,
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="",
        help="cli / start / --cli 进入终端模式；省略则开 GUI",
    )
    parser.add_argument(
        "--driver",
        choices=["local", "browser_use", "roxy", "chromium", "bu"],
        default=None,
        help="浏览器驱动: local | browser_use | roxy",
    )
    parser.add_argument(
        "--browser-use-key",
        dest="browser_use_key",
        default=None,
        help="Browser Use API Key（也可写在 config.json）",
    )
    parser.add_argument(
        "--country",
        default=None,
        help="Browser Use 代理国家代码，如 us / jp / sg / de",
    )
    parser.add_argument(
        "--no-bu-proxy",
        action="store_true",
        help="不向 Browser Use 传 proxyCountryCode",
    )
    parser.add_argument(
        "--bu-profile",
        default=None,
        help="Browser Use profileId（可选，复用 cookies）",
    )
    parser.add_argument(
        "--bu-timeout",
        type=int,
        default=None,
        help="Browser Use 会话超时（分钟，默认 15，最大 240）",
    )
    parser.add_argument(
        "--roxy-token",
        dest="roxy_token",
        default=None,
        help="RoxyBrowser API token（也可写在 config.json / ROXY_API_TOKEN）",
    )
    parser.add_argument(
        "--roxy-base",
        dest="roxy_base",
        default=None,
        help="Roxy API base，默认 http://127.0.0.1:50000",
    )
    parser.add_argument(
        "--roxy-workspace",
        dest="roxy_workspace",
        default=None,
        help="Roxy workspaceId（可留空自动探测）",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="注册数量（覆盖 config.register_count）",
    )
    parser.add_argument(
        "--capmonster-key",
        dest="capmonster_key",
        default=None,
        help="CapMonster Cloud API key（也可写在 config.json / CAPMONSTER_API_KEY）",
    )
    parser.add_argument(
        "--no-capmonster",
        action="store_true",
        help="禁用 CapMonster，仅本地点击 Turnstile",
    )
    args, unknown = parser.parse_known_args(argv)

    if args.driver:
        driver = args.driver.strip().lower()
        if driver in ("chromium", "local"):
            config["browser_driver"] = "local"
        elif driver in ("roxy", "roxybrowser"):
            config["browser_driver"] = "roxy"
        else:
            config["browser_driver"] = "browser_use"
    if args.browser_use_key is not None:
        config["browser_use_api_key"] = args.browser_use_key.strip()
    if args.country is not None:
        config["browser_use_proxy_country"] = args.country.strip().lower()
    if args.no_bu_proxy:
        config["browser_use_use_proxy"] = False
    if args.bu_profile is not None:
        config["browser_use_profile_id"] = args.bu_profile.strip()
    if args.bu_timeout is not None:
        config["browser_use_timeout_minutes"] = int(args.bu_timeout)
    if args.roxy_token is not None:
        config["roxy_api_token"] = args.roxy_token.strip()
    if args.roxy_base is not None:
        config["roxy_api_base"] = args.roxy_base.strip()
    if args.roxy_workspace is not None:
        config["roxy_workspace_id"] = args.roxy_workspace.strip()
    if args.count is not None:
        config["register_count"] = max(1, int(args.count))
    if args.capmonster_key is not None:
        config["capmonster_api_key"] = args.capmonster_key.strip()
    if args.no_capmonster:
        config["capmonster_enabled"] = False

    # Env fallback for Browser Use / Roxy / CapMonster keys
    if not str(config.get("browser_use_api_key") or "").strip():
        env_key = os.environ.get("BROWSER_USE_API_KEY", "").strip()
        if env_key:
            config["browser_use_api_key"] = env_key
    if not str(config.get("roxy_api_token") or "").strip():
        env_roxy = os.environ.get("ROXY_API_TOKEN", "").strip()
        if env_roxy:
            config["roxy_api_token"] = env_roxy
    if not str(config.get("roxy_api_base") or "").strip():
        env_base = os.environ.get("ROXY_API_BASE", "").strip()
        if env_base:
            config["roxy_api_base"] = env_base
    if not str(config.get("capmonster_api_key") or "").strip():
        env_cm = os.environ.get("CAPMONSTER_API_KEY", "").strip()
        if env_cm:
            config["capmonster_api_key"] = env_cm

    mode = (args.mode or "").strip().lower()
    if unknown:
        # allow legacy: python grok_register_ttk.py start
        for item in unknown:
            low = item.strip().lower()
            if low in ("start", "cli", "--cli"):
                mode = "cli"
    return mode


def main_cli():
    # config already loaded + CLI overrides applied in main()
    count = int(config.get("register_count", 1) or 1)
    driver = get_browser_driver()
    cli_log("[*] CLI 已加载配置")
    cli_log(
        f"[*] 邮箱服务商: {config.get('email_provider', 'duckmail')} | "
        f"驱动: {driver} | 注册数量: {count}"
    )
    if driver == "browser_use":
        if not str(config.get("browser_use_api_key") or "").strip():
            cli_log("[!] Browser Use 需要 browser_use_api_key（config / --browser-use-key / BROWSER_USE_API_KEY）")
            return
        cli_log(
            f"[*] Browser Use: country={config.get('browser_use_proxy_country') or '-'} "
            f"use_proxy={config.get('browser_use_use_proxy')} "
            f"timeout_min={config.get('browser_use_timeout_minutes', 15)} "
            f"profile={config.get('browser_use_profile_id') or '-'}"
        )
    if capmonster_is_enabled():
        cli_log(
            f"[*] CapMonster Cloud: enabled base={config.get('capmonster_api_base') or 'https://api.capmonster.cloud'} "
            f"timeout={config.get('capmonster_timeout', 120)}s "
            f"key=…{get_capmonster_api_key()[-6:] if len(get_capmonster_api_key())>=6 else 'set'}"
        )
    else:
        cli_log("[*] CapMonster: 未启用（填 capmonster_api_key 以云解 Turnstile）")
    if driver == "roxy":
        if not str(config.get("roxy_api_token") or "").strip():
            cli_log("[!] Roxy 需要 roxy_api_token（config / --roxy-token / ROXY_API_TOKEN）")
            return
        cli_log(
            f"[*] Roxy: api={config.get('roxy_api_base') or 'http://127.0.0.1:50000'} "
            f"workspace={config.get('roxy_workspace_id') or 'auto'} "
            f"one_profile={config.get('roxy_one_profile_per_account', True)} "
            f"delete_after={config.get('roxy_delete_profile_after_run', True)} "
            f"os={config.get('roxy_default_os') or 'Windows'}"
        )
    cli_log("[*] 输入 start 后开始；按 Ctrl+C 可强制停止")
    try:
        command = input("> ").strip().lower()
    except KeyboardInterrupt:
        cli_log("[!] 已取消")
        return
    if command != "start":
        cli_log("[!] 未输入 start，已退出")
        return
    try:
        run_registration_cli(count)
    except KeyboardInterrupt:
        # 清理阶段仍可能漏出，保证 CLI 干净退出
        cli_log("[!] 已停止")


def main():
    load_config()
    mode = _apply_cli_overrides(sys.argv[1:])
    if mode in ("start", "cli", "--cli"):
        main_cli()
        return
    root = tk.Tk()
    setup_light_theme(root)
    app = GrokRegisterGUI(root)
    root.mainloop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
