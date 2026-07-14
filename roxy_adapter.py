# -*- coding: utf-8 -*-
"""Connect DrissionPage Chromium to a RoxyBrowser profile via debuggerAddress.

Flow:
  1. Roxy API create (if one-profile-per-account) + open
  2. Attach DrissionPage with existing_only + set_address(debugger)
  3. On quit: detach locally, then close + delete Roxy profile
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from roxybrowser_client import RoxyBrowserClient, RoxyOpenResult

LogFn = Optional[Callable[[str], None]]


class RoxyChromium:
    """Thin wrapper so stop_browser() can cleanup Roxy profile after Chromium.quit()."""

    def __init__(
        self,
        chromium: Any,
        client: RoxyBrowserClient,
        opened: RoxyOpenResult,
        log_callback: LogFn = None,
    ):
        self._chromium = chromium
        self._client = client
        self._opened = opened
        self._log = log_callback
        self.driver = "roxy"
        self.roxy_profile_id = opened.profile_id
        self.roxy_debugger_address = opened.debugger_address
        self.roxy_created_by_run = bool(opened.created_by_run)

    def __getattr__(self, name: str):
        return getattr(self._chromium, name)

    def get_tabs(self):
        return self._chromium.get_tabs()

    def new_tab(self, *args, **kwargs):
        return self._chromium.new_tab(*args, **kwargs)

    def quit(self, del_data: bool = True):
        # Detach from CDP first (do not delete local user-data; Roxy owns the profile).
        try:
            self._chromium.quit(del_data=False)
        except BaseException:
            pass
        try:
            self._client.cleanup_profile(self._opened)
        except BaseException as exc:
            if self._log:
                try:
                    self._log(f"[Roxy] cleanup 异常: {exc}")
                except Exception:
                    pass
        self._chromium = None


def connect_roxy(config: dict, log_callback: LogFn = None) -> RoxyChromium:
    """Create/open a Roxy profile and attach DrissionPage to its debugger port."""
    from DrissionPage import Chromium, ChromiumOptions

    client = RoxyBrowserClient.from_config(config, log_callback=log_callback)
    opened = client.open_profile()

    address = opened.debugger_address
    if not address and opened.ws_endpoint:
        # DrissionPage set_address accepts ws:// and extracts netloc.
        address = opened.ws_endpoint
    if not address:
        try:
            client.cleanup_profile(opened)
        except Exception:
            pass
        raise RuntimeError(
            "Roxy open 未返回 debuggerAddress，无法用 DrissionPage 附着。"
            "请确认 Roxy API /browser/open 返回 http/debugger 字段。"
        )

    if log_callback:
        log_callback(
            f"[Roxy] 附着 Chromium debugger={address} "
            f"dirId={opened.profile_id} created={opened.created_by_run}"
        )

    options = ChromiumOptions()
    options.existing_only(True)
    options.set_address(address)
    options.set_load_mode("eager")
    options.set_timeouts(base=15, page_load=20, script=30)

    try:
        chromium = Chromium(options)
    except Exception:
        try:
            client.cleanup_profile(opened)
        except Exception:
            pass
        raise

    return RoxyChromium(
        chromium=chromium,
        client=client,
        opened=opened,
        log_callback=log_callback,
    )
