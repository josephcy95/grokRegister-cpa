# -*- coding: utf-8 -*-
"""Unit tests: multi-language CF hard-block classification + locale helpers."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import grok_register_ttk as app  # noqa: E402


class CloudflareBlockClassifyTests(unittest.TestCase):
    def test_attention_required_en_blocked(self):
        snap = {
            "url": "https://accounts.x.ai/sign-up",
            "title": "Attention Required! | Cloudflare",
            "body": (
                "Sorry, you have been blocked. You are unable to access accounts.x.ai. "
                "Cloudflare Ray ID: 9abc123. Performance & security by Cloudflare. "
                "Why have I been blocked?"
            ),
            "html_head": "<div id='cf-error-details'>error</div>",
            "dom_hits": ["#cf-error-details"],
            "big_cf_iframes": 0,
            "has_email_cta": False,
            "has_email_input": False,
            "has_profile": False,
            "has_cf_turnstile_field": False,
        }
        r = app.classify_cloudflare_page_block(snap)
        self.assertTrue(r["blocked"], r)
        self.assertGreaterEqual(r["score"], 6)

    def test_click_to_reveal_style_blocked(self):
        snap = {
            "url": "https://accounts.x.ai/cdn-cgi/challenge-platform/…",
            "title": "Just a moment...",
            "body": "Checking if the site connection is secure. Enable JavaScript and cookies to continue. Cloudflare Ray ID: xyz.",
            "html_head": "challenges.cloudflare.com",
            "dom_hits": ["#challenge-running"],
            "big_cf_iframes": 1,
            "has_email_cta": False,
            "has_email_input": False,
            "has_profile": False,
            "has_cf_turnstile_field": False,
        }
        r = app.classify_cloudflare_page_block(snap)
        self.assertTrue(r["blocked"], r)

    def test_jp_block_copy(self):
        snap = {
            "url": "https://accounts.x.ai/sign-up",
            "title": "アクセスが拒否されました",
            "body": "アクセスがブロックされました。このウェブサイトの所有者。セキュリティサービス Cloudflare Ray ID: abc",
            "html_head": "cloudflare",
            "dom_hits": ["#cf-wrapper"],
            "big_cf_iframes": 0,
            "has_email_cta": False,
            "has_email_input": False,
            "has_profile": False,
            "has_cf_turnstile_field": False,
        }
        r = app.classify_cloudflare_page_block(snap)
        self.assertTrue(r["blocked"], r)

    def test_cn_block_copy(self):
        snap = {
            "url": "https://accounts.x.ai/sign-up",
            "title": "访问被拒绝",
            "body": "您已被封锁。为什么我会被封锁。此网站使用安全服务。Cloudflare Ray ID: 123",
            "html_head": "cf-error",
            "dom_hits": [".cf-error-title"],
            "big_cf_iframes": 0,
            "has_email_cta": False,
            "has_email_input": False,
            "has_profile": False,
            "has_cf_turnstile_field": False,
        }
        r = app.classify_cloudflare_page_block(snap)
        self.assertTrue(r["blocked"], r)

    def test_real_signup_en_not_blocked(self):
        snap = {
            "url": "https://accounts.x.ai/sign-up?redirect=grok-com",
            "title": "Create Your Grok Account | Grok",
            "body": (
                "You are signing into Create your account Sign up with X "
                "Sign up with email Sign up with Apple Sign up with Google"
            ),
            "html_head": "<button>Sign up with email</button>",
            "dom_hits": [],
            "big_cf_iframes": 0,
            "has_email_cta": True,
            "has_email_input": False,
            "has_profile": False,
            "has_cf_turnstile_field": False,
        }
        r = app.classify_cloudflare_page_block(snap)
        self.assertFalse(r["blocked"], r)

    def test_real_signup_jp_not_blocked(self):
        snap = {
            "url": "https://accounts.x.ai/sign-up",
            "title": "Grok アカウントを作成 | Grok",
            "body": "にログインしています アカウントを作成 メールで登録 Google で登録",
            "html_head": "",
            "dom_hits": [],
            "big_cf_iframes": 0,
            "has_email_cta": True,
            "has_email_input": False,
            "has_profile": False,
            "has_cf_turnstile_field": False,
        }
        r = app.classify_cloudflare_page_block(snap)
        self.assertFalse(r["blocked"], r)

    def test_profile_step_with_turnstile_not_blocked(self):
        """Managed Turnstile on the form is NOT a hard block page."""
        snap = {
            "url": "https://accounts.x.ai/sign-up",
            "title": "Create Your Grok Account | Grok",
            "body": "Complete your sign up First name Last name Password",
            "html_head": "challenges.cloudflare.com/turnstile",
            "dom_hits": [],
            "big_cf_iframes": 0,
            "has_email_cta": False,
            "has_email_input": False,
            "has_profile": True,
            "has_cf_turnstile_field": True,
        }
        r = app.classify_cloudflare_page_block(snap)
        self.assertFalse(r["blocked"], r)
        self.assertEqual(r["kind"], "signup_form")

    def test_raise_if_blocked(self):
        # Monkeypatch snapshot source via classify input path
        logs = []

        def fake_snap():
            return {
                "url": "https://accounts.x.ai/sign-up",
                "title": "Attention Required! | Cloudflare",
                "body": "Sorry, you have been blocked. Cloudflare Ray ID: x. Performance & security by Cloudflare.",
                "html_head": "<div id='cf-error-details'></div>",
                "dom_hits": ["#cf-error-details"],
                "big_cf_iframes": 0,
                "has_email_cta": False,
                "has_email_input": False,
                "has_profile": False,
                "has_cf_turnstile_field": False,
            }

        old = app._page_text_blob_for_block_check
        app._page_text_blob_for_block_check = fake_snap
        try:
            with self.assertRaises(app.CloudflareBlockedError) as ctx:
                app.raise_if_cloudflare_blocked(log_callback=logs.append, context="test")
            self.assertIn("Cloudflare", str(ctx.exception))
            self.assertTrue(any("拦截" in x or "Cloudflare" in x for x in logs))
        finally:
            app._page_text_blob_for_block_check = old


class LocaleMarkerTests(unittest.TestCase):
    def test_email_signup_selectors_cover_major_locales(self):
        joined = "\n".join(app._EMAIL_SIGNUP_TEXT_SELECTORS)
        for needle in (
            "Sign up with email",
            "メールで登録",
            "使用邮箱",
            "이메일",
            "Mit E-Mail",
            "Registrarse",
            "สมัคร",
        ):
            self.assertIn(needle, joined, f"missing locale marker {needle}")

    def test_alive_markers_include_localized_signup(self):
        for m in ("sign up with email", "メールで登録", "使用邮箱注册", "mit e-mail registrieren"):
            self.assertIn(m, app._SIGNUP_ALIVE_MARKERS)


if __name__ == "__main__":
    unittest.main()
