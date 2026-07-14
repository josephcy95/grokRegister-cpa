# -*- coding: utf-8 -*-
"""RoxyBrowser local API client (create / open / close / delete profiles).

Mirrors turb-gpt-free-register/core/roxybrowser_client.py, driven by config dict
instead of a separate config module.

Official defaults:
  API base: http://127.0.0.1:50000
  All requests require a ``token`` header.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional
from urllib.parse import unquote, urljoin, urlparse

import requests

LogFn = Optional[Callable[[str], None]]


@dataclass
class RoxyOpenResult:
    profile_id: str
    raw: dict
    debugger_address: Optional[str] = None
    webdriver_url: Optional[str] = None
    ws_endpoint: Optional[str] = None
    created_by_run: bool = False


def _strip_slashes(value: str) -> str:
    return str(value or "").strip().strip("/")


def _join_url(base: str, path: str) -> str:
    return urljoin(base.rstrip("/") + "/", path.lstrip("/"))


def _mask_proxy(proxy_url: str) -> str:
    parsed = urlparse(str(proxy_url or "").strip())
    if parsed.username or parsed.password:
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://***:***@{host}{port}"
    return str(proxy_url or "").strip()


def _proxy_url_to_roxy_info(proxy_url: str, check_channel: str = "") -> dict:
    """Map http(s)/socks5://user:pass@host:port → Roxy proxyInfo payload."""
    text = str(proxy_url or "").strip()
    if not text:
        raise ValueError("代理为空")
    parsed = urlparse(text)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https", "socks5", "socks5h"):
        raise ValueError(f"Roxy 暂不支持该代理协议: {scheme or '-'}")
    if not parsed.hostname or not parsed.port:
        raise ValueError(f"代理格式缺少 host/port: {_mask_proxy(text)}")

    protocol = {
        "http": "HTTP",
        "https": "HTTPS",
        "socks5": "SOCKS5",
        "socks5h": "SOCKS5",
    }[scheme]
    info = {
        "moduleId": 0,
        "proxyMethod": "custom",
        "proxyCategory": protocol,
        "ipType": "IPV4",
        "protocol": protocol,
        "host": parsed.hostname,
        "port": str(parsed.port),
    }
    if parsed.username:
        info["proxyUserName"] = unquote(parsed.username)
    if parsed.password:
        info["proxyPassword"] = unquote(parsed.password)
    if str(check_channel or "").strip():
        info["checkChannel"] = str(check_channel).strip()
    return info


def _dig(payload: dict, *keys: str):
    cur = payload
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _first(payload: dict, paths: list[tuple[str, ...]]) -> str:
    for path in paths:
        value = _dig(payload, *path)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _as_id(value: Any) -> Any:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return int(raw) if raw.isdigit() else raw


def _normalize_profile_id(value: Any) -> str:
    text = str(value or "").strip()
    if text in ("-", "—", "无", "空", "none", "None", "null", "NULL"):
        return ""
    return text


def _log(log_callback: LogFn, msg: str) -> None:
    if log_callback:
        try:
            log_callback(msg)
        except Exception:
            pass


class RoxyBrowserClient:
    """HTTP client for RoxyBrowser local control API."""

    def __init__(self, config: dict | None = None, log_callback: LogFn = None):
        cfg = dict(config or {})
        self.config = cfg
        self.log_callback = log_callback

        self.api_base = str(
            cfg.get("roxy_api_base") or os.environ.get("ROXY_API_BASE") or "http://127.0.0.1:50000"
        ).strip()
        self.token = str(
            cfg.get("roxy_api_token") or os.environ.get("ROXY_API_TOKEN") or ""
        ).strip()

        self.workspace_id = str(cfg.get("roxy_workspace_id") or "").strip()
        self.project_id = str(cfg.get("roxy_project_id") or "").strip()
        self.profile_id_fixed = _normalize_profile_id(cfg.get("roxy_profile_id"))

        self.one_profile_per_account = bool(cfg.get("roxy_one_profile_per_account", True))
        self.delete_after_run = bool(cfg.get("roxy_delete_profile_after_run", True))
        self.keep_open = bool(cfg.get("roxy_keep_browser_open", False))
        self.open_headless = bool(cfg.get("roxy_open_headless", False))
        self.default_os = str(cfg.get("roxy_default_os") or "Windows").strip() or "Windows"
        self.default_os_version = str(cfg.get("roxy_default_os_version") or "").strip()
        self.create_use_proxy = bool(cfg.get("roxy_create_use_proxy", False))
        self.proxy_check_channel = str(cfg.get("roxy_proxy_check_channel") or "").strip()
        self.profile_name = str(cfg.get("roxy_profile_name") or "grok-register").strip() or "grok-register"
        self.api_timeout = max(5, int(cfg.get("roxy_api_timeout") or 90))
        self.api_retries = max(1, int(cfg.get("roxy_api_retries") or 3))
        self.api_retry_delay = max(0.5, float(cfg.get("roxy_api_retry_delay") or 2))

        self.create_path = str(cfg.get("roxy_create_path") or "/browser/create")
        self.create_method = str(cfg.get("roxy_create_method") or "POST").upper()
        self.open_path = str(cfg.get("roxy_open_path") or "/browser/open")
        self.open_method = str(cfg.get("roxy_open_method") or "POST").upper()
        self.close_path = str(cfg.get("roxy_close_path") or "/browser/close")
        self.close_method = str(cfg.get("roxy_close_method") or "POST").upper()
        self.delete_path = str(cfg.get("roxy_delete_path") or "/browser/delete")
        self.delete_method = str(cfg.get("roxy_delete_method") or "POST").upper()
        self.workspace_list_path = str(cfg.get("roxy_workspace_list_path") or "/browser/workspace")
        self.workspace_list_method = str(cfg.get("roxy_workspace_list_method") or "GET").upper()
        self.create_payload_extra = dict(cfg.get("roxy_profile_create_payload") or {})
        self.open_extra_params = dict(cfg.get("roxy_open_extra_params") or {})

        self.http = requests.Session()
        if self.token:
            self.http.headers.update(
                {
                    "token": self.token,
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                }
            )

    @classmethod
    def from_config(cls, config: dict, log_callback: LogFn = None) -> "RoxyBrowserClient":
        return cls(config=config, log_callback=log_callback)

    def _emit(self, msg: str) -> None:
        _log(self.log_callback, msg)

    @staticmethod
    def _is_retryable_error(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return any(
            x in text
            for x in (
                "timeout",
                "timed out",
                "connection",
                "temporarily",
                "http 500",
                "http 502",
                "http 503",
                "http 504",
                "http 429",
            )
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> dict:
        url = _join_url(self.api_base, path)
        method_u = method.upper()
        is_create = str(path or "").rstrip("/").endswith("/create") or "browser/create" in str(path or "")
        max_attempts = 1 if is_create else self.api_retries
        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.http.request(
                    method_u,
                    url,
                    params=params or None,
                    json=json_body if json_body is not None else None,
                    timeout=self.api_timeout,
                )
                text = resp.text or ""
                try:
                    payload = resp.json()
                except Exception:
                    payload = {"raw": text}
                if not (200 <= resp.status_code < 300):
                    raise RuntimeError(
                        f"Roxy API 请求失败 {method_u} {path} HTTP {resp.status_code}: {text[:500]}"
                    )
                if isinstance(payload, dict):
                    code = payload.get("code")
                    ok = payload.get("ok")
                    success = payload.get("success")
                    if code not in (None, 0, 200, "0", "200") and ok is not True and success is not True:
                        msg = (
                            payload.get("msg")
                            or payload.get("message")
                            or payload.get("error")
                            or json.dumps(payload, ensure_ascii=False)[:500]
                        )
                        raise RuntimeError(f"Roxy API 返回失败 {method_u} {path}: {msg}")
                return payload if isinstance(payload, dict) else {"data": payload}
            except Exception as exc:
                last_exc = exc
                if attempt >= max_attempts or not self._is_retryable_error(exc):
                    raise
                delay = self.api_retry_delay * attempt
                self._emit(
                    f"[Roxy] API 失败将重试 {method_u} {path} "
                    f"attempt={attempt}/{max_attempts} wait={delay:.1f}s err={exc}"
                )
                time.sleep(delay)
        raise last_exc or RuntimeError(f"Roxy API 请求失败 {method_u} {path}")

    def try_request(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
    ) -> tuple[bool, dict | str]:
        try:
            return True, self.request(method, path, params=params, json_body=json_body)
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"

    def ensure_token(self) -> None:
        if not self.token:
            raise RuntimeError(
                "roxy_api_token 为空。请在 config.json 填写，或设置环境变量 ROXY_API_TOKEN。"
            )

    def ensure_workspace_id(self) -> Any:
        """Return workspaceId, auto-detecting via /browser/workspace when empty."""
        if self.workspace_id:
            return _as_id(self.workspace_id)

        self._emit("[Roxy] workspaceId 未配置，尝试从 API 自动获取…")
        items = self.list_workspaces().get("items") or []
        if not items:
            raise RuntimeError(
                "Roxy 需要 workspaceId。请在 Roxy 客户端查看工作区 ID，"
                "写入 config.json 的 roxy_workspace_id，或确保 API 可列出工作区。"
            )
        first = items[0]
        wid = str(first.get("id") or "").strip()
        if not wid:
            raise RuntimeError(f"Roxy 工作区列表解析失败: {items[:3]}")
        self.workspace_id = wid
        # Prefer first project's id when available
        pid = str(first.get("projectId") or "").strip()
        if pid and not self.project_id:
            self.project_id = pid
        self._emit(
            f"[Roxy] 自动选用工作区: {first.get('label') or wid}"
            + (f" project={pid}" if pid else "")
        )
        return _as_id(wid)

    @staticmethod
    def _extract_workspace_items(payload: dict) -> list[dict]:
        out: list[dict] = []
        rows = None
        if isinstance(payload, dict):
            data = payload.get("data")
            if isinstance(data, dict):
                rows = data.get("rows") or data.get("list") or data.get("records")
            elif isinstance(data, list):
                rows = data
        if isinstance(rows, list):
            for row in rows:
                if not isinstance(row, dict):
                    continue
                wid = row.get("id") or row.get("workspaceId") or row.get("workspace_id")
                wname = (
                    row.get("workspaceName")
                    or row.get("workspace_name")
                    or row.get("name")
                    or str(wid or "")
                )
                projects = (
                    row.get("project_details")
                    or row.get("projectDetails")
                    or row.get("projects")
                    or []
                )
                if isinstance(projects, list) and projects:
                    for proj in projects:
                        if not isinstance(proj, dict):
                            continue
                        pid = proj.get("projectId") or proj.get("project_id") or proj.get("id")
                        pname = (
                            proj.get("projectName")
                            or proj.get("project_name")
                            or proj.get("name")
                            or str(pid or "")
                        )
                        if wid:
                            out.append(
                                {
                                    "id": str(wid),
                                    "name": str(wname),
                                    "projectId": str(pid or ""),
                                    "projectName": str(pname or ""),
                                    "label": (
                                        f"{wname} / {pname} ({wid}/{pid})"
                                        if pid
                                        else f"{wname} ({wid})"
                                    ),
                                }
                            )
                elif wid:
                    out.append(
                        {
                            "id": str(wid),
                            "name": str(wname),
                            "projectId": "",
                            "projectName": "",
                            "label": f"{wname} ({wid})",
                        }
                    )
        return out

    def list_workspaces(self) -> dict:
        candidates = [
            (self.workspace_list_method, self.workspace_list_path),
            ("GET", "/browser/workspace"),
            ("POST", "/browser/workspace"),
            ("GET", "/workspace/list"),
            ("GET", "/browser/workspace/list"),
        ]
        seen = set()
        errors = []
        for method, path in candidates:
            key = (method, path)
            if key in seen or not path:
                continue
            seen.add(key)
            ok, payload = self.try_request(method, path)
            if not ok:
                errors.append({"method": method, "path": path, "error": payload})
                continue
            items = self._extract_workspace_items(payload if isinstance(payload, dict) else {})
            if items:
                return {"ok": True, "path": path, "method": method, "items": items, "raw": payload}
            errors.append({"method": method, "path": path, "error": "empty items", "payload": payload})
        return {"ok": False, "items": [], "errors": errors}

    def create_profile(self, payload: dict | None = None) -> str:
        self.ensure_token()
        body = dict(self.create_payload_extra)
        body.setdefault("name", self.profile_name)
        if self.default_os:
            body.setdefault("os", self.default_os)
        if self.default_os_version:
            body.setdefault("osVersion", self.default_os_version)

        workspace_id = self.ensure_workspace_id()
        body.setdefault("workspaceId", workspace_id)
        project_id = _as_id(self.project_id)
        if project_id:
            body.setdefault("projectId", project_id)

        if self.create_use_proxy and not body.get("proxyInfo"):
            proxy_url = str(self.config.get("proxy") or "").strip()
            if proxy_url:
                try:
                    body["proxyInfo"] = _proxy_url_to_roxy_info(
                        proxy_url, check_channel=self.proxy_check_channel
                    )
                    self._emit(f"[Roxy] 创建环境写入代理: {_mask_proxy(proxy_url)}")
                except Exception as exc:
                    self._emit(f"[Roxy] 代理解析失败，跳过: {exc}")
            else:
                self._emit("[Roxy] roxy_create_use_proxy=true 但 config.proxy 为空")

        if payload:
            body.update(payload)
        if not body.get("workspaceId"):
            raise RuntimeError("Roxy 创建环境需要 workspaceId（roxy_workspace_id）")

        self._emit(
            f"[Roxy] 创建环境 workspaceId={body.get('workspaceId')} "
            f"projectId={body.get('projectId') or '-'} os={body.get('os') or '-'}"
        )
        result = self.request(self.create_method, self.create_path, json_body=body)
        profile_id = _first(
            result,
            [
                ("id",),
                ("dirId",),
                ("dir_id",),
                ("profile_id",),
                ("profileId",),
                ("browser_id",),
                ("data", "id"),
                ("data", "dirId"),
                ("data", "dir_id"),
                ("data", "profile_id"),
                ("data", "profileId"),
                ("data", "browser_id"),
            ],
        )
        if not profile_id:
            raise RuntimeError(f"Roxy 创建环境成功但未返回 dirId/profile_id: {result}")
        self._emit(f"[Roxy] 已创建环境 dirId={profile_id}")
        return profile_id

    def open_profile(self, profile_id: str | None = None) -> RoxyOpenResult:
        self.ensure_token()
        one_profile = self.one_profile_per_account
        configured_pid = _normalize_profile_id(
            profile_id if profile_id is not None else self.profile_id_fixed
        )
        if one_profile and configured_pid:
            raise RuntimeError(
                "已启用 roxy_one_profile_per_account=true（一号一环境），"
                "不能配置/传入固定 roxy_profile_id；请留空以便每个账号创建新环境。"
            )

        pid = configured_pid
        created_by_run = False
        if not pid:
            pid = self.create_profile()
            created_by_run = True

        workspace_id = self.ensure_workspace_id()
        params = dict(self.open_extra_params)
        params.setdefault("workspaceId", workspace_id)
        params.setdefault("dirId", _as_id(pid))
        params.setdefault("args", [])
        params.setdefault("forceOpen", True)
        params["headless"] = self.open_headless

        path = self.open_path.format(profile_id=pid)
        self._emit(
            f"[Roxy] 打开环境 dirId={pid} headless={params.get('headless')} "
            f"one_profile={one_profile}"
        )
        result = self.request(
            self.open_method,
            path,
            params=params if self.open_method == "GET" else None,
            json_body=params if self.open_method != "GET" else None,
        )
        debugger_address = self._extract_debugger_address(result)
        webdriver_url = (
            _first(
                result,
                [
                    ("webdriver",),
                    ("webDriver",),
                    ("webdriver_url",),
                    ("webdriverUrl",),
                    ("selenium",),
                    ("selenium_url",),
                    ("seleniumUrl",),
                    ("data", "webdriver"),
                    ("data", "webDriver"),
                    ("data", "webdriver_url"),
                    ("data", "webdriverUrl"),
                    ("data", "selenium"),
                    ("data", "selenium_url"),
                    ("data", "seleniumUrl"),
                ],
            )
            or None
        )
        ws_endpoint = (
            _first(
                result,
                [
                    ("ws",),
                    ("wsEndpoint",),
                    ("ws_endpoint",),
                    ("debuggerWsUrl",),
                    ("data", "ws"),
                    ("data", "wsEndpoint"),
                    ("data", "ws_endpoint"),
                    ("data", "debuggerWsUrl"),
                ],
            )
            or None
        )
        if not debugger_address and not webdriver_url and not ws_endpoint:
            raise RuntimeError(
                f"Roxy 已打开环境但未返回 debuggerAddress/webdriver/ws，请检查接口响应: "
                f"{json.dumps(result, ensure_ascii=False)[:800]}"
            )
        self._emit(
            f"[Roxy] open 成功 debugger={debugger_address or '-'} "
            f"ws={bool(ws_endpoint)} webdriver={bool(webdriver_url)}"
        )
        return RoxyOpenResult(
            profile_id=pid,
            raw=result,
            debugger_address=debugger_address,
            webdriver_url=webdriver_url,
            ws_endpoint=ws_endpoint,
            created_by_run=created_by_run,
        )

    def close_profile(self, profile_id: str) -> None:
        if not profile_id:
            return
        path = self.close_path.format(profile_id=profile_id)
        try:
            body = {
                "workspaceId": self.ensure_workspace_id(),
                "dirId": _as_id(profile_id),
            }
            self.request(
                self.close_method,
                path,
                params=body if self.close_method == "GET" else None,
                json_body=body if self.close_method != "GET" else None,
            )
            self._emit(f"[Roxy] 已关闭环境: {profile_id}")
        except Exception as exc:
            self._emit(f"[Roxy] 关闭环境失败: {exc}")

    def delete_profile(self, profile_id: str) -> None:
        if not profile_id:
            return
        path = self.delete_path.format(profile_id=profile_id)
        try:
            body = {
                "workspaceId": self.ensure_workspace_id(),
                "dirIds": [_as_id(profile_id)],
            }
            self.request(
                self.delete_method,
                path,
                params=body if self.delete_method == "GET" else None,
                json_body=body if self.delete_method != "GET" else None,
            )
            self._emit(f"[Roxy] 已删除环境: {profile_id}")
        except Exception as exc:
            self._emit(f"[Roxy] 删除环境失败: {exc}")

    def cleanup_profile(self, opened: RoxyOpenResult | None) -> None:
        """Close window; with one-profile-per-account, delete the profile created this run."""
        if not opened or not opened.profile_id:
            return
        if not self.keep_open:
            self.close_profile(opened.profile_id)

        should_delete = (
            self.one_profile_per_account
            and self.delete_after_run
            and bool(opened.created_by_run)
        )
        if should_delete:
            if self.keep_open:
                self._emit(
                    f"[Roxy] keep_open=true，跳过删除环境: {opened.profile_id}"
                )
                return
            self.delete_profile(opened.profile_id)

    @staticmethod
    def _extract_debugger_address(payload: dict) -> str | None:
        value = _first(
            payload,
            [
                ("debuggerAddress",),
                ("debugger_address",),
                ("debugAddress",),
                ("debuggingPortUrl",),
                ("debugging_port_url",),
                ("remoteDebuggingAddress",),
                ("remote_debugging_address",),
                ("http",),
                ("debugHttp",),
                ("debug_http",),
                ("data", "debuggerAddress"),
                ("data", "debugger_address"),
                ("data", "debugAddress"),
                ("data", "debuggingPortUrl"),
                ("data", "debugging_port_url"),
                ("data", "remoteDebuggingAddress"),
                ("data", "remote_debugging_address"),
                ("data", "http"),
                ("data", "debugHttp"),
                ("data", "debug_http"),
            ],
        )
        if value:
            value = value.strip()
            value = value.replace("http://", "").replace("https://", "").strip("/")
            if value.startswith(":") and value[1:].isdigit():
                return f"127.0.0.1{value}"
            if value.isdigit():
                return f"127.0.0.1:{value}"
            if ":" in value and not value.startswith(":"):
                return value
        port = _first(
            payload,
            [
                ("debuggingPort",),
                ("debugging_port",),
                ("debug_port",),
                ("port",),
                ("data", "debuggingPort"),
                ("data", "debugging_port"),
                ("data", "debug_port"),
                ("data", "port"),
            ],
        )
        if port:
            port = str(port).strip()
            if port.startswith(":"):
                port = port[1:]
            if port.isdigit():
                return f"127.0.0.1:{port}"
        return None
