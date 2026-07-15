# -*- coding: utf-8 -*-
"""Unit tests for standardized run output layout."""
from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import run_output as ro


class BuildRunIdTests(unittest.TestCase):
    def test_browser_use_jp_capmonster(self):
        cfg = {
            "browser_driver": "browser_use",
            "browser_use_use_proxy": True,
            "browser_use_proxy_country": "jp",
            "capmonster_enabled": True,
            "capmonster_api_key": "cm-test",
            "proxy": "",
        }
        rid = ro.build_run_id(cfg, stamp="20260715_120000")
        self.assertEqual(
            rid,
            "20260715_120000__browser_use__capmonster__proxy-jp",
        )

    def test_local_no_proxy_localclick(self):
        cfg = {
            "browser_driver": "local",
            "proxy": "",
            "capmonster_enabled": False,
            "capmonster_api_key": "",
        }
        rid = ro.build_run_id(cfg, stamp="20260715_120000")
        self.assertEqual(
            rid,
            "20260715_120000__local__localclick__proxy-none",
        )

    def test_forced_run_id(self):
        cfg = {"output_run_id": "my-fixed-run"}
        self.assertEqual(ro.build_run_id(cfg), "my-fixed-run")

    def test_suffix(self):
        cfg = {
            "browser_driver": "local",
            "proxy": "",
            "capmonster_api_key": "",
            "capmonster_enabled": False,
            "output_run_suffix": "w2",
        }
        rid = ro.build_run_id(cfg, stamp="20260715_120000")
        self.assertTrue(rid.endswith("__w2"))

    def test_proxy_url_masked_in_describe(self):
        cfg = {
            "browser_driver": "local",
            "proxy": "http://user:secret@127.0.0.1:7890",
        }
        tag, info = ro.describe_proxy(cfg)
        self.assertEqual(tag, "proxy-http")
        self.assertIn("***", info["config_proxy"])
        self.assertNotIn("secret", info["config_proxy"])


class BeginEndRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="run_out_test_"))
        self.cfg = {
            "browser_driver": "browser_use",
            "browser_use_use_proxy": True,
            "browser_use_proxy_country": "jp",
            "capmonster_enabled": True,
            "capmonster_api_key": "cm-test",
            "proxy": "",
            "output_root": str(self.tmp),
            "register_count": 2,
            "register_success_target": 1,
            "cpa_auto_add": True,
            "email_provider": "cloudflare",
            "defaultDomains": "example.com",
        }

    def tearDown(self):
        # Clear active run if test left one
        with ro._lock:
            ro._active = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_begin_creates_layout_and_latest(self):
        logs = []
        run = ro.begin_run(
            self.cfg,
            log_callback=logs.append,
            note_template="Browser Use - Capmonster - BrowserUse:jp proxy",
        )
        self.assertTrue(run.run_dir.is_dir())
        self.assertTrue((run.run_dir / "cpa").is_dir())
        self.assertTrue(run.run_json_path.is_file())
        self.assertTrue(run.accounts_sso_path.is_file())
        self.assertTrue(run.mail_credentials_path.is_file())
        self.assertTrue(run.register_log_path.is_file())
        meta = json.loads(run.run_json_path.read_text(encoding="utf-8"))
        self.assertEqual(meta["driver"], "browser_use")
        self.assertEqual(meta["captcha_solver"], "capmonster")
        self.assertEqual(meta["proxy"]["country"], "jp")
        self.assertIn("Browser Use", meta["note_template"])
        latest = self.tmp / "latest"
        self.assertTrue(latest.is_symlink() or latest.exists())
        self.assertIs(ro.get_active_run(), run)

        run.append_mail_credential("a@example.com", "jwt-token")
        run.append_success("a@example.com", "pass", "sso-token", profile={"given_name": "A"})
        run.append_failure("register", "boom", email="b@example.com", slot=2)
        run.append_log_line("hello log")

        self.assertIn("a@example.com\tjwt-token", run.mail_credentials_path.read_text())
        self.assertIn("a@example.com----pass----sso-token", run.accounts_sso_path.read_text())
        self.assertIn("sso-token", run.accounts_jsonl_path.read_text())
        self.assertIn("boom", run.failed_path.read_text())
        self.assertIn("hello log", run.register_log_path.read_text())

        ended = ro.end_run(succeeded=1, failed=1, log_callback=logs.append)
        self.assertIs(ended, run)
        self.assertIsNone(ro.get_active_run())
        summary = json.loads(run.summary_path.read_text(encoding="utf-8"))
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertTrue(any("Run output" in m or "Run summary" in m for m in logs))

    def test_cpa_write_dirs_includes_run_and_config(self):
        auth = self.tmp / "extra_auth"
        auth.mkdir()
        self.cfg["cpa_auth_dir"] = str(auth)
        self.cfg["cpa_mirror_dir"] = str(self.tmp / "mirror")
        run = ro.begin_run(self.cfg)
        dirs = ro.cpa_write_dirs(self.cfg)
        resolved = {str(p.resolve()) if p.exists() else str(p) for p in dirs}
        self.assertIn(str(run.cpa_dir.resolve()), resolved)
        self.assertIn(str(auth.resolve()), resolved)
        # mirror may not exist yet — still listed
        self.assertTrue(any("mirror" in str(p) for p in dirs))
        ro.end_run(succeeded=0, failed=0)


class OutputTreeCommittedTests(unittest.TestCase):
    def test_gitkeep_present(self):
        root = Path(__file__).resolve().parents[1]
        self.assertTrue((root / "output" / ".gitkeep").is_file())
        self.assertTrue((root / "output" / "runs" / ".gitkeep").is_file())


if __name__ == "__main__":
    unittest.main()
