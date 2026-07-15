# -*- coding: utf-8 -*-
"""Unit tests for CPA export note (proxy must appear when Browser Use JP proxy is on)."""
import unittest

import grok_register_ttk as app


class BuildCpaExportNoteTests(unittest.TestCase):
    def setUp(self):
        self._orig = app.config.copy()

    def tearDown(self):
        app.config = self._orig

    def test_browser_use_jp_capmonster_mentions_proxy(self):
        app.config = app.DEFAULT_CONFIG.copy()
        app.config["browser_driver"] = "browser_use"
        app.config["browser_use_use_proxy"] = True
        app.config["browser_use_proxy_country"] = "jp"
        app.config["capmonster_enabled"] = True
        app.config["capmonster_api_key"] = "test-key-not-empty"
        app.config["proxy"] = ""

        note = app.build_cpa_export_note()
        self.assertIn("Browser Use", note)
        self.assertIn("Capmonster", note)
        # Must mention proxy / JP — not bare "No Proxy"
        self.assertNotIn("No Proxy", note)
        self.assertTrue(
            "BrowserUse:jp" in note or "jp" in note.lower() and "proxy" in note.lower(),
            f"note should mention JP proxy, got {note!r}",
        )
        self.assertIn("proxy", note.lower())

    def test_local_no_proxy_says_no_proxy(self):
        app.config = app.DEFAULT_CONFIG.copy()
        app.config["browser_driver"] = "local"
        app.config["proxy"] = ""
        app.config["capmonster_enabled"] = False
        app.config["capmonster_api_key"] = ""
        note = app.build_cpa_export_note()
        self.assertIn("Local", note)
        self.assertIn("No Proxy", note)
        self.assertNotIn("Capmonster", note)

    def test_browser_use_client_jp_query(self):
        from browser_use_client import BrowserUseClient

        client = BrowserUseClient(
            api_key="bu_test",
            proxy_country_code="jp",
            use_proxy=True,
        )
        session = client.build_connect_url()
        self.assertEqual(session.proxy_country_code, "jp")
        self.assertIn("proxyCountryCode=jp", session.connect_url)

    def test_playwright_tab_has_run_cdp(self):
        """Browser Use path must expose run_cdp (email CTA / mouse Input)."""
        from browser_use_adapter import PlaywrightTab

        self.assertTrue(hasattr(PlaywrightTab, "run_cdp"))
        self.assertTrue(callable(getattr(PlaywrightTab, "run_cdp")))


if __name__ == "__main__":
    unittest.main()
