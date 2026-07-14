# -*- coding: utf-8 -*-
"""Playwright page adapter so Browser Use Cloud can reuse DrissionPage-style helpers.

DrissionPage cannot attach to Browser Use's remote WSS CDP endpoint.
This adapter exposes the subset of the DrissionPage Tab API used by
grok_register_ttk.py (run_js / get / cookies / html / url / ele / actions).
"""
from __future__ import annotations

import re
import time
from typing import Any, Optional


class _WaitProxy:
    def __init__(self, page: "PlaywrightTab"):
        self._page = page

    def doc_loaded(self, timeout=8, raise_err=False):
        try:
            # Playwright has no direct "doc_loaded"; poll readyState.
            deadline = time.time() + max(float(timeout or 0), 0.1)
            while time.time() < deadline:
                state = self._page.run_js("return document.readyState;")
                if state in ("interactive", "complete"):
                    return True
                time.sleep(0.15)
            if raise_err:
                raise TimeoutError("document.readyState wait timed out")
            return False
        except Exception:
            if raise_err:
                raise
            return False


class _ActionsProxy:
    def __init__(self, page: "PlaywrightTab"):
        self._page = page
        self._target = None

    def move_to(self, ele):
        self._target = ele
        return self

    def click(self):
        target = self._target
        if target is None:
            return self
        try:
            target.click(by_js=False)
        except Exception:
            try:
                target.click(by_js=True)
            except Exception:
                pass
        return self


class _ScrollProxy:
    def __init__(self, element: "PlaywrightElement"):
        self._element = element

    def to_see(self):
        handle = self._element._handle
        if handle is None:
            return self
        try:
            handle.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            try:
                handle.evaluate("el => el.scrollIntoView({block:'center', inline:'nearest'})")
            except Exception:
                pass
        return self


class PlaywrightElement:
    """Minimal element shim for click / run_js / shadow-ish access."""

    def __init__(self, page: "PlaywrightTab", selector: str = "", handle=None):
        self._page = page
        self._selector = selector
        self._handle = handle
        self.scroll = _ScrollProxy(self)

    def click(self, by_js: bool = False):
        if self._handle is not None:
            if by_js:
                self._handle.evaluate("el => el.click()")
            else:
                self._handle.click(timeout=5000)
            return True
        if self._selector:
            loc = self._page._page.locator(self._selector).first
            if by_js:
                loc.evaluate("el => el.click()")
            else:
                loc.click(timeout=5000)
            return True
        raise RuntimeError("PlaywrightElement has no target")

    @property
    def text(self) -> str:
        if self._handle is None:
            return ""
        try:
            return str(self._handle.inner_text(timeout=1000) or "")
        except Exception:
            try:
                return str(self._handle.evaluate("el => el.innerText || el.textContent || ''") or "")
            except Exception:
                return ""

    def run_js(self, script: str, *args):
        # Element-scoped evaluate; support both function body and expression.
        if self._handle is None:
            raise RuntimeError("element handle missing")
        body = _rewrite_arguments(script)
        packed = _pack_js_args(args)
        if packed is not None:
            return self._handle.evaluate(f"(el, __args) => {{ {body} }}", packed)
        return self._handle.evaluate(f"(el) => {{ {body} }}")

    @property
    def parent(self) -> "PlaywrightElement":
        if self._handle is None:
            return PlaywrightElement(self._page)
        parent_handle = self._handle.evaluate_handle("el => el.parentElement")
        return PlaywrightElement(self._page, handle=parent_handle.as_element())

    @property
    def shadow_root(self) -> "PlaywrightShadowRoot":
        return PlaywrightShadowRoot(self._page, self._handle)

    def ele(self, locator: str, timeout: float = 2):
        # Limited support: tag:iframe / tag:input / tag:body
        tag = _parse_tag_locator(locator)
        if not tag or self._handle is None:
            return None
        handle = self._handle.evaluate_handle(
            """(el, tag) => {
                const root = el.shadowRoot || el;
                return root.querySelector(tag) || null;
            }""",
            tag,
        )
        element = handle.as_element()
        if element is None:
            return None
        return PlaywrightElement(self._page, handle=element)


class PlaywrightShadowRoot:
    def __init__(self, page: "PlaywrightTab", host_handle):
        self._page = page
        self._host = host_handle

    def ele(self, locator: str, timeout: float = 2):
        tag = _parse_tag_locator(locator)
        if not tag or self._host is None:
            return None
        handle = self._host.evaluate_handle(
            """(el, tag) => {
                const root = el.shadowRoot;
                if (!root) return null;
                return root.querySelector(tag) || null;
            }""",
            tag,
        )
        element = handle.as_element()
        if element is None:
            return None
        return PlaywrightElement(self._page, handle=element)


class PlaywrightTab:
    """Tab-like object with DrissionPage-compatible helpers used by registration."""

    def __init__(self, browser: "PlaywrightBrowser", page):
        self._browser = browser
        self._page = page
        self.wait = _WaitProxy(self)
        self.actions = _ActionsProxy(self)

    @property
    def url(self) -> str:
        try:
            return str(self._page.url or "")
        except Exception:
            return ""

    @property
    def html(self) -> str:
        try:
            return str(self._page.content() or "")
        except Exception:
            return ""

    def get(self, url: str, timeout: float = 20):
        # Domcontentloaded mirrors local eager load mode.
        self._page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=max(float(timeout or 20), 1) * 1000,
        )
        return self

    def run_js(self, script: str, *args):
        body = _rewrite_arguments(script)
        packed = _pack_js_args(args)
        if packed is not None:
            # When first arg is an ElementHandle (e.g. scrollIntoView), use a
            # function that receives it as arguments[0] equivalent.
            if len(args) == 1 and isinstance(args[0], PlaywrightElement) and args[0]._handle is not None:
                # Body already rewrote arguments[0] -> __args[0]; for single element
                # pass as __args array with the element as first item via evaluate_handle path.
                handle = args[0]._handle
                # Prefer element-scoped call: inject as arguments[0] via rewrite reverse.
                body_el = re.sub(r"\b__args\[0\]", "el", body)
                body_el = re.sub(r"\barguments\[0\]", "el", body_el)
                return handle.evaluate(f"(el) => {{ {body_el} }}")
            return self._page.evaluate(f"(__args) => {{ {body} }}", packed)
        return self._page.evaluate(f"() => {{ {body} }}")

    def cookies(self, all_domains: bool = True, all_info: bool = True):
        try:
            return list(self._browser.context.cookies() or [])
        except Exception:
            return []

    def ele(self, locator: str, timeout: float = 2):
        results = self.eles(locator, timeout=timeout)
        return results[0] if results else None

    def eles(self, locator: str, timeout: float = 2):
        selector, engine = _drission_locator_to_playwright(locator)
        if not selector:
            return []
        try:
            if engine == "xpath":
                loc = self._page.locator(f"xpath={selector}")
            elif engine == "text":
                loc = self._page.get_by_text(selector, exact=False)
            else:
                loc = self._page.locator(selector)
            try:
                loc.first.wait_for(state="attached", timeout=max(float(timeout or 0), 0.1) * 1000)
            except Exception:
                pass
            count = loc.count()
            out = []
            for i in range(min(count, 30)):
                try:
                    handle = loc.nth(i).element_handle(timeout=500)
                    if handle is not None:
                        out.append(PlaywrightElement(self, selector=selector, handle=handle))
                except Exception:
                    continue
            return out
        except Exception:
            return []


class PlaywrightBrowser:
    """Browser-like object (connect_over_cdp session)."""

    def __init__(self, playwright, browser, context, page, session_info=None, keep_open=False):
        self._playwright = playwright
        self._browser = browser
        self.context = context
        self._page = page
        self.session_info = session_info
        self.keep_open = keep_open
        self.user_data_path = None
        self.driver = "browser_use"
        self._tabs = [PlaywrightTab(self, page)]
        self.network_events: list[dict] = []
        self._wire_network_hooks(page)

    def _wire_network_hooks(self, page) -> None:
        """Capture interesting API responses for signup debugging."""
        try:
            def on_response(response):
                try:
                    url = response.url or ""
                    if not any(
                        k in url
                        for k in (
                            "x.ai",
                            "accounts.",
                            "auth",
                            "sign-up",
                            "signup",
                            "register",
                            "turnstile",
                            "challenge",
                        )
                    ):
                        return
                    status = response.status
                    # Keep only failures and signup-ish endpoints
                    interesting = status >= 400 or any(
                        k in url for k in ("sign", "auth", "register", "challenge", "create")
                    )
                    if not interesting:
                        return
                    body_snip = ""
                    try:
                        if status >= 400 or "json" in (response.headers.get("content-type") or ""):
                            text = response.text()
                            body_snip = (text or "")[:240]
                    except Exception:
                        body_snip = ""
                    self.network_events.append(
                        {
                            "status": status,
                            "url": url[:200],
                            "body": body_snip,
                        }
                    )
                    # Cap memory
                    if len(self.network_events) > 40:
                        self.network_events = self.network_events[-40:]
                except Exception:
                    pass

            page.on("response", on_response)
        except Exception:
            pass

    def recent_network_failures(self, limit: int = 8) -> list[dict]:
        events = list(self.network_events or [])
        fails = [e for e in events if int(e.get("status") or 0) >= 400]
        return (fails or events)[-limit:]

    def get_tabs(self):
        # Refresh from live context pages when possible.
        try:
            pages = list(self.context.pages or [])
            if pages:
                self._tabs = [PlaywrightTab(self, p) for p in pages]
                self._page = pages[-1]
        except Exception:
            pass
        return list(self._tabs)

    def get_tab(self, index: int = 0):
        tabs = self.get_tabs()
        if not tabs:
            return self.new_tab()
        if index < 0 or index >= len(tabs):
            return tabs[-1]
        return tabs[index]

    def new_tab(self, url: Optional[str] = None):
        page = self.context.new_page()
        tab = PlaywrightTab(self, page)
        self._tabs.append(tab)
        self._page = page
        if url:
            tab.get(url, timeout=30)
        return tab

    def quit(self, del_data: bool = True):
        # Browser Use sessions stop when the websocket disconnects.
        try:
            if not self.keep_open and self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                self._playwright.stop()
        except Exception:
            pass
        self._browser = None
        self._playwright = None


def connect_browser_use(
    config: dict,
    log_callback=None,
    navigation_timeout: int | None = None,
) -> PlaywrightBrowser:
    """Connect Playwright to Browser Use Cloud stealth Chromium over CDP."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "缺少 playwright。请执行: uv pip install playwright "
            "（Browser Use 远端浏览器无需 playwright install chromium）"
        ) from exc

    from browser_use_client import BrowserUseClient

    client = BrowserUseClient.from_config(config)
    session = client.open_session()
    if log_callback:
        log_callback(
            f"[BrowserUse] 连接 CDP proxyCountry={session.proxy_country_code or '-'} "
            f"profileId={session.profile_id or '-'} base={session.raw.get('base')}"
        )

    timeout_s = int(navigation_timeout or config.get("browser_use_nav_timeout", 90) or 90)
    timeout_ms = max(5, timeout_s) * 1000
    keep_open = bool(config.get("browser_use_keep_open", False))

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
        )
        if log_callback:
            log_callback("[BrowserUse] CDP 已连接（stealth Chromium）")
        return wrapper
    except Exception:
        try:
            pw.stop()
        except Exception:
            pass
        raise


def _rewrite_arguments(script: str) -> str:
    """Map DrissionPage-style arguments[N] to Playwright evaluate __args[N]."""
    return re.sub(r"\barguments\[(\d+)\]", r"__args[\1]", script)


def _pack_js_args(args: tuple) -> Optional[list]:
    if not args:
        return None
    packed = []
    for arg in args:
        if isinstance(arg, PlaywrightElement):
            # Plain values only for multi-arg evaluate; single-element path is special-cased.
            packed.append(None)
        else:
            packed.append(arg)
    return packed


def _parse_tag_locator(locator: str) -> str:
    text = str(locator or "").strip()
    if text.startswith("tag:"):
        return text.split(":", 1)[1].strip() or ""
    if re.fullmatch(r"[a-zA-Z][\w-]*", text):
        return text
    return ""


def _drission_locator_to_playwright(locator: str) -> tuple[str, str]:
    """Return (selector, engine) where engine is css|xpath|text."""
    text = str(locator or "").strip()
    if not text:
        return "", "css"
    if text.startswith("text:"):
        return text.split(":", 1)[1].strip(), "text"
    if text.startswith("xpath:"):
        return text.split(":", 1)[1].strip(), "xpath"
    if text.startswith("//") or text.startswith("(//"):
        return text, "xpath"
    # @name=cf-turnstile-response
    m = re.match(r"@([a-zA-Z_:][\w:.-]*)=(.*)$", text)
    if m:
        attr, value = m.group(1), m.group(2)
        value = value.strip().strip('"').strip("'")
        return f'[{attr}="{value}"]', "css"
    if text.startswith("tag:"):
        return text.split(":", 1)[1], "css"
    if text.startswith("css:"):
        return text.split(":", 1)[1], "css"
    return text, "css"
