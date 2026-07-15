# -*- coding: utf-8 -*-
"""Browserbase Cloud client: create session + CDP connect URL + release.

Docs: https://docs.browserbase.com/reference/api/create-a-session
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from curl_cffi import requests as http

DEFAULT_API_BASE = "https://api.browserbase.com/v1"
DEFAULT_REGION = "us-west-2"
VALID_REGIONS = frozenset(
    {"us-west-2", "us-east-1", "eu-central-1", "ap-southeast-1"}
)


@dataclass
class BrowserbaseSession:
    session_id: str
    connect_url: str
    region: str = DEFAULT_REGION
    project_id: str = ""
    proxy_country: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    api_key: str = ""  # kept for release; never log

    def safe_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "region": self.region,
            "project_id": self.project_id,
            "proxy_country": self.proxy_country,
            "connect_url_present": bool(self.connect_url),
        }


class BrowserbaseClient:
    def __init__(
        self,
        api_key: str = "",
        *,
        api_base: str = DEFAULT_API_BASE,
        project_id: str = "",
        region: str = DEFAULT_REGION,
        use_proxy: bool = False,
        proxy_country: str = "",
        proxy_city: str = "",
        timeout_seconds: int | None = None,
        solve_captchas: bool = True,
        advanced_stealth: bool = False,
    ):
        self.api_key = (api_key or "").strip()
        self.api_base = (api_base or DEFAULT_API_BASE).rstrip("/")
        self.project_id = (project_id or "").strip()
        region = (region or DEFAULT_REGION).strip()
        self.region = region if region in VALID_REGIONS else DEFAULT_REGION
        self.use_proxy = bool(use_proxy)
        self.proxy_country = (proxy_country or "").strip().upper()
        self.proxy_city = (proxy_city or "").strip()
        self.timeout_seconds = timeout_seconds
        self.solve_captchas = bool(solve_captchas)
        self.advanced_stealth = bool(advanced_stealth)
        self._session: Optional[BrowserbaseSession] = None

    @classmethod
    def from_config(cls, config: dict) -> "BrowserbaseClient":
        import os

        key = str(config.get("browserbase_api_key") or "").strip()
        if not key:
            key = os.environ.get("BROWSERBASE_API_KEY", "").strip()
        timeout_min = config.get("browserbase_timeout_minutes")
        timeout_s = None
        try:
            if timeout_min not in (None, ""):
                # Free tier max ~15 min; clamp later on create
                timeout_s = max(60, min(int(float(timeout_min) * 60), 21600))
        except (TypeError, ValueError):
            timeout_s = None
        return cls(
            api_key=key,
            api_base=str(config.get("browserbase_api_base") or DEFAULT_API_BASE),
            project_id=str(config.get("browserbase_project_id") or ""),
            region=str(config.get("browserbase_region") or DEFAULT_REGION),
            use_proxy=bool(config.get("browserbase_use_proxy", False)),
            proxy_country=str(config.get("browserbase_proxy_country") or ""),
            proxy_city=str(config.get("browserbase_proxy_city") or ""),
            timeout_seconds=timeout_s,
            solve_captchas=bool(config.get("browserbase_solve_captchas", True)),
            advanced_stealth=bool(config.get("browserbase_advanced_stealth", False)),
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "browserbase_api_key 为空。请到 Browserbase Settings 创建 API Key，"
                "并在 config.json / --bb-key / BROWSERBASE_API_KEY 中填写。"
            )
        return self.api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-BB-API-Key": self.require_api_key(),
        }

    def build_create_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "region": self.region,
            "browserSettings": {
                "solveCaptchas": self.solve_captchas,
            },
        }
        if self.project_id:
            payload["projectId"] = self.project_id
        if self.advanced_stealth:
            payload["browserSettings"]["advancedStealth"] = True
        if self.timeout_seconds is not None:
            payload["timeout"] = int(self.timeout_seconds)
        if self.use_proxy:
            if self.proxy_country:
                geo: dict[str, str] = {"country": self.proxy_country}
                if self.proxy_city:
                    geo["city"] = self.proxy_city
                payload["proxies"] = [
                    {"type": "browserbase", "geolocation": geo}
                ]
            else:
                payload["proxies"] = True
        return payload

    def create_session(self) -> BrowserbaseSession:
        payload = self.build_create_payload()
        url = f"{self.api_base}/sessions"
        resp = http.post(url, headers=self._headers(), json=payload, timeout=90)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Browserbase create session failed HTTP {resp.status_code}: "
                f"{(resp.text or '')[:400]}"
            )
        data = resp.json()
        sid = str(data.get("id") or "").strip()
        connect = str(data.get("connectUrl") or data.get("connect_url") or "").strip()
        if not sid or not connect:
            raise RuntimeError(
                f"Browserbase session response missing id/connectUrl: {list(data.keys())}"
            )
        session = BrowserbaseSession(
            session_id=sid,
            connect_url=connect,
            region=str(data.get("region") or self.region),
            project_id=str(data.get("projectId") or self.project_id),
            proxy_country=self.proxy_country if self.use_proxy else "",
            raw={
                k: data.get(k)
                for k in (
                    "status",
                    "region",
                    "expiresAt",
                    "startedAt",
                    "proxyBytes",
                    "keepAlive",
                )
            },
            api_key=self.api_key,
        )
        self._session = session
        return session

    def release_session(self, session_id: str | None = None) -> None:
        sid = (session_id or (self._session.session_id if self._session else "")).strip()
        if not sid:
            return
        url = f"{self.api_base}/sessions/{sid}"
        try:
            http.post(
                url,
                headers=self._headers(),
                json={"status": "REQUEST_RELEASE"},
                timeout=30,
            )
        except Exception:
            try:
                http.delete(url, headers=self._headers(), timeout=30)
            except Exception:
                pass
        if self._session and self._session.session_id == sid:
            self._session = None

    def open_session(self) -> BrowserbaseSession:
        return self.create_session()
