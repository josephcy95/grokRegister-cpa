#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Browser Use country probe with forced hard reload after first paint.

If a country dies on reload (CF hard block), register path is unlikely to work
because grok_register_ttk always hard-reloads after first signup paint.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from browser_use_client import BrowserUseClient  # noqa: E402

SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

# Prior multi-country set minus sg/au (already failed register under reload).
# Use uk (not gb) — Browser Use enum rejected gb with 422.
COUNTRIES: list[tuple[str, str]] = [
    ("jp", "Japan"),
    ("us", "United States"),
    ("tw", "Taiwan"),
    ("hk", "Hong Kong"),
    ("uk", "United Kingdom"),
    ("nz", "New Zealand"),
    ("th", "Thailand"),
    ("de", "Germany"),
]

CF_HARD_MARKERS = (
    "attention required",
    "sorry, you have been blocked",
    "you have been blocked",
    "you are unable to access",
    "cf-error-details",
    "access denied",
)
SIGNUP_MARKERS = (
    "sign up with email",
    "sign up with x",
    "create your",
    "create your account",
    "メールで登録",
    "使用邮箱注册",
    "使用郵箱註冊",
    "mit e-mail registrieren",
)


def load_api_key() -> str:
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    key = (cfg.get("browser_use_api_key") or "").strip()
    if not key:
        raise SystemExit("browser_use_api_key missing in config.json")
    return key


def page_signals(page) -> dict:
    title = ""
    url = ""
    body = ""
    try:
        title = page.title() or ""
    except Exception as exc:
        title = f"<title err: {exc}>"
    try:
        url = page.url or ""
    except Exception as exc:
        url = f"<url err: {exc}>"
    try:
        body = page.evaluate(
            """() => {
              const t = (document.body && document.body.innerText) || '';
              return t.replace(/\\s+/g, ' ').trim().slice(0, 500);
            }"""
        ) or ""
    except Exception as exc:
        body = f"<body err: {exc}>"
    low = f"{title}\n{body}".lower()
    hard = any(m in low for m in CF_HARD_MARKERS)
    if "cloudflare" in low and ("blocked" in low or "attention required" in low):
        hard = True
    try:
        has_cf_dom = bool(
            page.evaluate(
                "() => !!(document.querySelector('#cf-error-details, #cf-wrapper, .cf-error-details'))"
            )
        )
    except Exception:
        has_cf_dom = False
    if has_cf_dom and ("blocked" in low or "attention required" in low or "cloudflare" in title.lower()):
        hard = True
    signupish = any(m in low for m in SIGNUP_MARKERS)
    return {
        "title": title[:200],
        "url": url[:300],
        "body_snippet": body[:400],
        "cf_hard_block": hard,
        "signup_markers": signupish,
        "cf_dom": has_cf_dom,
    }


def classify_pair(first: dict, after: dict, error: str = "") -> str:
    if error and not first.get("url"):
        return "CONNECT_OR_NAV_ERROR"
    if first.get("cf_hard_block") and after.get("cf_hard_block"):
        return "CF_HARD_BOTH"
    if after.get("cf_hard_block"):
        return "CF_HARD_ON_RELOAD"
    if first.get("cf_hard_block"):
        return "CF_HARD_FIRST_ONLY"
    if after.get("signup_markers") and first.get("signup_markers"):
        return "SURVIVES_RELOAD"
    if after.get("signup_markers"):
        return "RELOAD_OK_FIRST_UNCLEAR"
    if first.get("signup_markers") and not after.get("signup_markers"):
        return "RELOAD_LOST_SIGNUP"
    return "UNCLEAR"


def probe_country(api_key: str, code: str, name: str, log) -> dict:
    t0 = time.time()
    result = {
        "country": code,
        "country_name": name,
        "status": "",
        "elapsed_s": 0.0,
        "error": "",
        "ip_hint": "",
        "first": {},
        "after_reload": {},
    }
    client = BrowserUseClient(
        api_key=api_key,
        proxy_country_code=code,
        use_proxy=True,
        session_timeout_minutes=12,
    )
    session = client.open_session()
    log(f"  connect proxyCountryCode={code}")
    pw = None
    browser = None
    try:
        pw = sync_playwright().start()
        browser = pw.chromium.connect_over_cdp(session.connect_url, timeout=90_000)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.pages[0] if context.pages else context.new_page()
        page.set_default_timeout(60_000)
        page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        time.sleep(1.2)
        first = page_signals(page)
        result["first"] = first
        log(
            f"  first: title={first['title']!r} cf_hard={first['cf_hard_block']} "
            f"signup={first['signup_markers']}"
        )

        # Forced hard reload — mirrors register path
        try:
            page.reload(wait_until="domcontentloaded", timeout=60_000)
        except Exception as exc:
            # fallback full nav
            log(f"  reload err, fallback goto: {exc}")
            page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=60_000)
        try:
            page.wait_for_load_state("networkidle", timeout=12_000)
        except Exception:
            pass
        time.sleep(1.5)
        after = page_signals(page)
        result["after_reload"] = after
        log(
            f"  after: title={after['title']!r} cf_hard={after['cf_hard_block']} "
            f"signup={after['signup_markers']}"
        )

        # optional IP hint
        try:
            ip_page = context.new_page()
            ip_page.goto("https://api.ipify.org?format=json", timeout=15_000)
            result["ip_hint"] = (ip_page.inner_text("body") or "")[:80]
            ip_page.close()
        except Exception as exc:
            result["ip_hint"] = f"<ip fail: {exc}>"

        result["status"] = classify_pair(first, after)
    except Exception as exc:
        msg = str(exc)
        # collapse huge 422 bodies
        msg = re.sub(r"\s+", " ", msg)[:500]
        result["error"] = msg
        result["status"] = classify_pair(
            result.get("first") or {},
            result.get("after_reload") or {},
            error=msg,
        )
        if not result["status"] or result["status"] == "UNCLEAR":
            result["status"] = "CONNECT_OR_NAV_ERROR"
        log(f"  → ERROR {msg[:200]}")
    finally:
        result["elapsed_s"] = round(time.time() - t0, 1)
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            if pw:
                pw.stop()
        except Exception:
            pass
    log(f"  status={result['status']} elapsed={result['elapsed_s']}s ip={result['ip_hint']}")
    return result


def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "output" / "campaign_logs"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"bu_reload_probe_{stamp}.log"
    json_path = out_dir / f"bu_reload_probe_{stamp}.json"
    api_key = load_api_key()

    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        lines.append(msg)

    log(f"Reload probe started {datetime.now(timezone.utc).isoformat()}")
    log(f"URL={SIGNUP_URL}")
    log(f"Countries (skip sg/au): {[c for c, _ in COUNTRIES]}")
    log("")

    results = []
    for code, name in COUNTRIES:
        log(f"=== {code} ({name}) ===")
        results.append(probe_country(api_key, code, name, log))
        log("")

    survives = [r["country"] for r in results if r["status"] in ("SURVIVES_RELOAD", "RELOAD_OK_FIRST_UNCLEAR")]
    die_reload = [r["country"] for r in results if r["status"] == "CF_HARD_ON_RELOAD"]
    hard_both = [r["country"] for r in results if r["status"] in ("CF_HARD_BOTH", "CF_HARD_FIRST_ONLY")]
    other = [
        r["country"]
        for r in results
        if r["country"] not in survives + die_reload + hard_both
    ]

    summary = {
        "survives_reload": survives,
        "cf_hard_on_reload": die_reload,
        "cf_hard_first_or_both": hard_both,
        "other": other,
    }
    payload = {
        "started": datetime.now(timezone.utc).isoformat(),
        "url": SIGNUP_URL,
        "skipped": ["sg", "au"],
        "countries": COUNTRIES,
        "results": results,
        "summary": summary,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log("=== SUMMARY ===")
    log(f"survives_reload: {survives}")
    log(f"cf_hard_on_reload: {die_reload}")
    log(f"cf_hard_first_or_both: {hard_both}")
    log(f"other: {other}")
    log(f"saved {json_path}")
    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nlog: {log_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
