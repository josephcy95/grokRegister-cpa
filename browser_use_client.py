# -*- coding: utf-8 -*-
"""Browser Use Cloud client: build stealth CDP websocket URL.

Docs:
  - https://docs.browser-use.com/cloud/browser/stealth
  - https://docs.browser-use.com/cloud/browser/playwright-puppeteer-selenium

Same pattern as turb-gpt-free-register/core/browser_use_client.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode


DEFAULT_CDP_BASE = "wss://connect.browser-use.com"


@dataclass
class BrowserUseSession:
    connect_url: str
    api_key_present: bool
    proxy_country_code: str = ""
    profile_id: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class BrowserUseClient:
    """Minimal client: official connect_over_cdp websocket."""

    def __init__(
        self,
        api_key: str = "",
        *,
        cdp_base: str = DEFAULT_CDP_BASE,
        proxy_country_code: str = "",
        use_proxy: bool = True,
        profile_id: str = "",
        session_timeout_minutes: int | None = None,
        browser_screen_width: int | None = None,
        browser_screen_height: int | None = None,
        extra_query: dict | None = None,
    ):
        self.api_key = (api_key or "").strip()
        self.cdp_base = (cdp_base or DEFAULT_CDP_BASE).rstrip("?&")
        self.proxy_country_code = (proxy_country_code or "").strip().lower()
        self.use_proxy = bool(use_proxy)
        self.profile_id = (profile_id or "").strip()
        self.session_timeout_minutes = session_timeout_minutes
        self.browser_screen_width = browser_screen_width
        self.browser_screen_height = browser_screen_height
        self.extra_query = dict(extra_query or {})

    @classmethod
    def from_config(cls, config: dict) -> "BrowserUseClient":
        return cls(
            api_key=str(config.get("browser_use_api_key", "") or ""),
            cdp_base=str(config.get("browser_use_cdp_base", DEFAULT_CDP_BASE) or DEFAULT_CDP_BASE),
            proxy_country_code=str(config.get("browser_use_proxy_country", "") or ""),
            use_proxy=bool(config.get("browser_use_use_proxy", True)),
            profile_id=str(config.get("browser_use_profile_id", "") or ""),
            session_timeout_minutes=_as_optional_int(config.get("browser_use_timeout_minutes")),
            browser_screen_width=_as_optional_int(config.get("browser_use_screen_width")),
            browser_screen_height=_as_optional_int(config.get("browser_use_screen_height")),
            extra_query=config.get("browser_use_extra_query") or {},
        )

    def require_api_key(self) -> str:
        if not self.api_key:
            raise RuntimeError(
                "browser_use_api_key 为空。请到 Browser Use Cloud 创建 API Key，"
                "并在 config.json / GUI 中填写。"
            )
        return self.api_key

    def build_connect_url(self) -> BrowserUseSession:
        api_key = self.require_api_key()
        query: dict[str, str] = {"apiKey": api_key}

        if self.use_proxy and self.proxy_country_code:
            query["proxyCountryCode"] = self.proxy_country_code

        if self.profile_id:
            query["profileId"] = self.profile_id

        if self.session_timeout_minutes is not None:
            # Browser Use docs: timeout is session length in minutes (default 15, max 240)
            minutes = max(1, min(int(self.session_timeout_minutes), 240))
            query["timeout"] = str(minutes)

        if self.browser_screen_width:
            query["browserScreenWidth"] = str(int(self.browser_screen_width))
        if self.browser_screen_height:
            query["browserScreenHeight"] = str(int(self.browser_screen_height))

        for key, value in self.extra_query.items():
            if value is None:
                continue
            text = str(value).strip()
            if text:
                query[str(key)] = text

        connect_url = f"{self.cdp_base}?{urlencode(query)}"
        safe_query = dict(query)
        if "apiKey" in safe_query:
            safe_query["apiKey"] = safe_query["apiKey"][:6] + "***"
        return BrowserUseSession(
            connect_url=connect_url,
            api_key_present=True,
            proxy_country_code=self.proxy_country_code,
            profile_id=self.profile_id,
            raw={"query": safe_query, "base": self.cdp_base},
        )

    def open_session(self) -> BrowserUseSession:
        return self.build_connect_url()


def _as_optional_int(value) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
