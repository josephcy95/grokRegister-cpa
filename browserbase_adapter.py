# -*- coding: utf-8 -*-
"""Browserbase Cloud adapter: CDP via Playwright, reuse PlaywrightTab helpers."""
from __future__ import annotations

from typing import Optional

from browser_use_adapter import PlaywrightBrowser
from browserbase_client import BrowserbaseClient


def connect_browserbase(
    config: dict,
    log_callback=None,
    navigation_timeout: int | None = None,
) -> PlaywrightBrowser:
    """Create a Browserbase session and attach Playwright over CDP."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "缺少 playwright。请执行: uv pip install playwright "
            "（Browserbase 远端浏览器无需 playwright install chromium）"
        ) from exc

    client = BrowserbaseClient.from_config(config)
    session = client.open_session()
    if log_callback:
        log_callback(
            f"[Browserbase] session={session.session_id[:8]}… "
            f"region={session.region} "
            f"proxy_country={session.proxy_country or '-'} "
            f"status={session.raw.get('status') or '-'}"
        )

    timeout_s = int(
        navigation_timeout
        or config.get("browserbase_nav_timeout")
        or config.get("browser_use_nav_timeout")
        or 90
        or 90
    )
    timeout_ms = max(5, timeout_s) * 1000
    keep_open = bool(config.get("browserbase_keep_open", False))

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(session.connect_url)
        if browser.contexts:
            context = browser.contexts[0]
        else:
            context = browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(timeout_ms)
        page.set_default_navigation_timeout(timeout_ms)
        wrapper = PlaywrightBrowser(
            playwright=pw,
            browser=browser,
            context=context,
            page=page,
            session_info=session,
            keep_open=keep_open,
            driver_name="browserbase",
        )
        # Attach client so quit() can release the cloud session
        wrapper._browserbase_client = client  # type: ignore[attr-defined]
        wrapper._browserbase_session_id = session.session_id  # type: ignore[attr-defined]
        if log_callback:
            log_callback("[Browserbase] CDP 已连接")
        return wrapper
    except Exception:
        try:
            client.release_session(session.session_id)
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass
        raise
