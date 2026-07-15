# -*- coding: utf-8 -*-
"""CLI --domain / --run-suffix / --config override tests."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import grok_register_ttk as app


class CliDomainFlagTests(unittest.TestCase):
    def setUp(self):
        self._orig = dict(app.config)

    def tearDown(self):
        app.config.clear()
        app.config.update(self._orig)

    def test_domain_and_run_suffix_override(self):
        app.config.clear()
        app.config.update(app.DEFAULT_CONFIG)
        app.config["defaultDomains"] = "old.example.com"
        app.config["output_run_suffix"] = ""
        mode = app._apply_cli_overrides(
            [
                "cli",
                "--domain",
                "yuyunailart.com",
                "--run-suffix",
                "yuyu",
                "--country",
                "jp",
                "--count",
                "15",
                "--success-target",
                "10",
                "-y",
            ]
        )
        self.assertEqual(mode, "cli")
        self.assertEqual(app.config["defaultDomains"], "yuyunailart.com")
        self.assertEqual(app.config["output_run_suffix"], "yuyu")
        self.assertEqual(app.config["browser_use_proxy_country"], "jp")
        self.assertEqual(app.config["register_count"], 15)
        self.assertEqual(app.config["register_success_target"], 10)
        self.assertTrue(app.config.get("_cli_auto_start"))

    def test_config_file_then_domain_wins(self):
        app.config.clear()
        app.config.update(app.DEFAULT_CONFIG)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "alt.json"
            path.write_text(
                json.dumps(
                    {
                        "defaultDomains": "from-file.example",
                        "browser_driver": "browser_use",
                        "browser_use_proxy_country": "us",
                    }
                ),
                encoding="utf-8",
            )
            app._apply_cli_overrides(
                [
                    "cli",
                    "--config",
                    str(path),
                    "--domain",
                    "flyovernow.ccwu.cc",
                    "--run-suffix",
                    "flyover",
                ]
            )
        self.assertEqual(app.config["defaultDomains"], "flyovernow.ccwu.cc")
        self.assertEqual(app.config["output_run_suffix"], "flyover")
        self.assertEqual(app.config["browser_driver"], "browser_use")
        self.assertEqual(app.config.get("_cli_config_path"), str(path.resolve()))


if __name__ == "__main__":
    unittest.main()
