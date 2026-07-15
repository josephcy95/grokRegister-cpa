"""Unit tests for submitOAuth2Consent Next-Action auto-discovery."""
from __future__ import annotations

import sso_to_auth_json as s2c


SAMPLE_CHUNK = (
    'let s=(0,i.createServerReference)("401b73e22a5e68737d0037e1aa449fef82cd1b35fb"'
    ',i.callServer,void 0,i.findSourceMapURL,"submitOAuth2Consent");'
)
SAMPLE_CHUNK_ALT = (
    'createServerReference("aabbccddeeff00112233445566778899aabbccdd",'
    'callServer,void 0,findSourceMapURL,"submitOAuth2Consent")'
)


def test_extract_submit_oauth2_consent_id_primary():
    assert (
        s2c._extract_submit_oauth2_consent_id(SAMPLE_CHUNK)
        == "401b73e22a5e68737d0037e1aa449fef82cd1b35fb"
    )


def test_extract_submit_oauth2_consent_id_alt():
    assert (
        s2c._extract_submit_oauth2_consent_id(SAMPLE_CHUNK_ALT)
        == "aabbccddeeff00112233445566778899aabbccdd"
    )


def test_extract_ignores_unrelated_actions():
    js = (
        'createServerReference)("004050e5c3e2fde2c7cc40922cef1b491a38b821c8",'
        'r.callServer,void 0,r.findSourceMapURL,"getSession")'
    )
    assert s2c._extract_submit_oauth2_consent_id(js) is None


def test_iter_consent_script_urls_absolute():
    html = (
        '<script src="/_next/static/chunks/a.js"></script>'
        '<script src="https://accounts.x.ai/_next/static/chunks/b.js"></script>'
        '<script src="/other.js"></script>'
    )
    urls = s2c._iter_consent_script_urls(html, "https://accounts.x.ai/oauth2/consent")
    assert urls == [
        "https://accounts.x.ai/_next/static/chunks/a.js",
        "https://accounts.x.ai/_next/static/chunks/b.js",
    ]


def test_consent_action_not_found_detection():
    assert s2c._consent_action_not_found(404, "Server action not found.")
    assert s2c._consent_action_not_found(200, "Server action not found")
    assert not s2c._consent_action_not_found(200, '{"success":true,"code":"x"}')


def test_discover_from_html_only():
    s2c._cached_next_action_id = None
    found = s2c.discover_consent_next_action_id(
        session=None,  # unused when id is in HTML
        consent_html=f"<html>{SAMPLE_CHUNK}</html>",
        consent_url="https://accounts.x.ai/oauth2/consent",
        log=lambda *_a, **_k: None,
    )
    assert found == "401b73e22a5e68737d0037e1aa449fef82cd1b35fb"
    assert s2c._cached_next_action_id == found


def test_discover_from_script_fetch():
    s2c._cached_next_action_id = None

    class FakeResp:
        def __init__(self, text):
            self.text = text
            self.url = "https://accounts.x.ai/_next/static/chunks/x.js"

    class FakeSession:
        def get(self, url, **_kwargs):
            assert "_next/static" in url
            return FakeResp(SAMPLE_CHUNK)

    html = '<script src="/_next/static/chunks/x.js"></script>'
    found = s2c.discover_consent_next_action_id(
        session=FakeSession(),
        consent_html=html,
        consent_url="https://accounts.x.ai/oauth2/consent",
        log=lambda *_a, **_k: None,
    )
    assert found == "401b73e22a5e68737d0037e1aa449fef82cd1b35fb"


def test_parse_consent_code_rsc_lines():
    body = (
        '0:{"a":"$@1"}\n'
        '1:{"success":true,"action":"allow","code":"abc123"}\n'
    )
    assert s2c._parse_consent_code(body) == "abc123"
