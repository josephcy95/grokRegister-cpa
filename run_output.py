# -*- coding: utf-8 -*-
"""Standardized registration run output under output/runs/<run_id>/.

Layout (one folder per registration job)::

    output/runs/
      20260715_001530__browser_use__capmonster__proxy-jp/
        run.json
        accounts.sso.txt
        accounts.jsonl
        mail_credentials.txt
        register.log
        failed.txt
        summary.json
        cpa/
          xai-<email>.json

Folder tags encode driver / captcha solver / proxy so runs are browsable
without opening run.json. Secrets never go in the folder name.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "output" / "runs"

_lock = threading.RLock()
_active: Optional["RunOutput"] = None


def _safe_tag(value: str, fallback: str = "x") -> str:
    text = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip().lower())
    text = re.sub(r"-{2,}", "-", text).strip("-._")
    return text[:48] if text else fallback


def describe_solver(cfg: dict) -> str:
    """Filesystem tag: capmonster | localclick | none."""
    key = str(cfg.get("capmonster_api_key") or "").strip()
    enabled = cfg.get("capmonster_enabled", True) is not False
    if key and enabled:
        return "capmonster"
    # Local click path (extension / CDP) when CapMonster off
    if cfg.get("capmonster_fallback_click", True) or not key:
        # If key missing or disabled, registration uses local click
        if not key or not enabled:
            return "localclick"
    return "none"


def describe_proxy(cfg: dict) -> tuple[str, dict]:
    """Return (folder tag like proxy-jp, structured proxy info for run.json)."""
    driver = str(cfg.get("browser_driver") or "local").strip().lower()
    if driver in ("browseruse", "browser-use", "bu", "cloud"):
        driver = "browser_use"
    if driver in ("browserbase", "bb", "browser_base", "browser-base"):
        driver = "browserbase"
    config_proxy = str(cfg.get("proxy") or "").strip()
    info: dict[str, Any] = {
        "mode": "none",
        "country": "",
        "use_proxy": False,
        "config_proxy": "",
    }
    # Never put credentials into folder names
    if config_proxy:
        info["mode"] = "http"
        info["config_proxy"] = _mask_proxy_url(config_proxy)
        info["use_proxy"] = True
        return "proxy-http", info
    if driver == "browser_use" and cfg.get("browser_use_use_proxy", True):
        country = str(cfg.get("browser_use_proxy_country") or "").strip().lower()
        info["mode"] = "browser_use_country"
        info["country"] = country
        info["use_proxy"] = True
        tag = f"proxy-{_safe_tag(country, 'bu')}" if country else "proxy-bu"
        return tag, info
    if driver == "browserbase":
        region = str(cfg.get("browserbase_region") or "us-west-2").strip().lower()
        info["region"] = region
        if cfg.get("browserbase_use_proxy"):
            country = str(cfg.get("browserbase_proxy_country") or "").strip().lower()
            info["mode"] = "browserbase_proxy"
            info["country"] = country
            info["use_proxy"] = True
            tag = f"proxy-{_safe_tag(country, 'bb')}" if country else "proxy-bb"
            return tag, info
        info["mode"] = "browserbase_region"
        info["use_proxy"] = False
        return f"region-{_safe_tag(region, 'usw2')}", info
    if driver == "roxy" and cfg.get("roxy_create_use_proxy"):
        info["mode"] = "roxy"
        info["use_proxy"] = bool(config_proxy)
        info["config_proxy"] = _mask_proxy_url(config_proxy) if config_proxy else ""
        return "proxy-roxy" if config_proxy else "proxy-none", info
    return "proxy-none", info


def _mask_proxy_url(proxy_url: str) -> str:
    """Hide user:pass in proxy URL for run.json."""
    text = str(proxy_url or "").strip()
    if not text or "@" not in text:
        return text
    try:
        # scheme://user:pass@host:port → scheme://***@host:port
        left, right = text.rsplit("@", 1)
        if "://" in left:
            scheme = left.split("://", 1)[0]
            return f"{scheme}://***@{right}"
        return f"***@{right}"
    except Exception:
        return "***"


def describe_driver(cfg: dict) -> str:
    raw = str(cfg.get("browser_driver") or "local").strip().lower()
    if raw in ("browseruse", "browser-use", "bu", "cloud"):
        return "browser_use"
    if raw in ("browserbase", "bb", "browser_base", "browser-base"):
        return "browserbase"
    if raw in ("roxy", "roxybrowser"):
        return "roxy"
    if raw in ("chromium", "local", ""):
        return "local"
    return _safe_tag(raw, "local")


def build_run_id(cfg: dict, stamp: Optional[str] = None) -> str:
    """Build ``YYYYMMDD_HHMMSS__driver__solver__proxy[-tag]`` run folder name."""
    forced = str(cfg.get("output_run_id") or "").strip()
    if forced:
        return _safe_tag(forced, forced) if re.match(r"^[\w.-]+$", forced) else forced

    if not stamp:
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    driver = describe_driver(cfg)
    solver = describe_solver(cfg)
    proxy_tag, _ = describe_proxy(cfg)
    parts = [stamp, driver, solver, proxy_tag]
    suffix = str(cfg.get("output_run_suffix") or "").strip()
    if suffix:
        parts.append(_safe_tag(suffix, "w"))
    return "__".join(parts)


def resolve_output_root(cfg: dict) -> Path:
    raw = str(cfg.get("output_root") or "").strip()
    if not raw:
        return DEFAULT_OUTPUT_ROOT
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path


class RunOutput:
    """Filesystem handle for one registration campaign."""

    def __init__(self, run_dir: Path, meta: dict):
        self.run_dir = run_dir
        self.meta = meta
        self.run_id = str(meta.get("run_id") or run_dir.name)
        self.accounts_sso_path = run_dir / "accounts.sso.txt"
        self.accounts_jsonl_path = run_dir / "accounts.jsonl"
        self.mail_credentials_path = run_dir / "mail_credentials.txt"
        self.failed_path = run_dir / "failed.txt"
        self.register_log_path = run_dir / "register.log"
        self.run_json_path = run_dir / "run.json"
        self.summary_path = run_dir / "summary.json"
        self.cpa_dir = run_dir / "cpa"
        self._started = datetime.datetime.now(datetime.timezone.utc)

    @property
    def accounts_output_file(self) -> str:
        """Path string used by legacy call sites expecting accounts_*.txt path."""
        return str(self.accounts_sso_path)

    def append_mail_credential(self, email: str, token: str) -> None:
        with _lock:
            with open(self.mail_credentials_path, "a", encoding="utf-8") as fh:
                fh.write(f"{email}\t{token}\n")

    def append_success(
        self,
        email: str,
        password: str,
        sso: str,
        *,
        profile: Optional[dict] = None,
        extra: Optional[dict] = None,
    ) -> None:
        line = f"{email}----{password}----{sso}\n"
        record = {
            "email": email,
            "password": password,
            "sso": sso,
            "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "driver": self.meta.get("driver"),
            "solver": self.meta.get("captcha_solver"),
            "proxy": self.meta.get("proxy"),
            "note": self.meta.get("note_template"),
        }
        if profile:
            record["profile"] = {
                "given_name": profile.get("given_name"),
                "family_name": profile.get("family_name"),
            }
        if extra:
            record.update(extra)
        with _lock:
            with open(self.accounts_sso_path, "a", encoding="utf-8") as fh:
                fh.write(line)
            with open(self.accounts_jsonl_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_failure(
        self,
        stage: str,
        error: str,
        *,
        email: str = "",
        slot: int | None = None,
    ) -> None:
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        parts = [ts, email or "-", stage or "-", str(error).replace("\n", " ")[:500]]
        if slot is not None:
            parts.insert(1, f"slot={slot}")
        with _lock:
            with open(self.failed_path, "a", encoding="utf-8") as fh:
                fh.write(" | ".join(parts) + "\n")

    def append_log_line(self, message: str) -> None:
        """Mirror a console log line into register.log."""
        try:
            with _lock:
                with open(self.register_log_path, "a", encoding="utf-8") as fh:
                    fh.write(str(message).rstrip() + "\n")
        except Exception:
            pass

    def write_summary(
        self,
        *,
        succeeded: int,
        failed: int,
        attempted_slots: int | None = None,
        extra: Optional[dict] = None,
    ) -> Path:
        finished = datetime.datetime.now(datetime.timezone.utc)
        duration = max(0.0, (finished - self._started).total_seconds())
        summary = {
            "run_id": self.run_id,
            "succeeded": int(succeeded),
            "failed": int(failed),
            "attempted_slots": attempted_slots
            if attempted_slots is not None
            else int(succeeded) + int(failed),
            "duration_sec": round(duration, 1),
            "started_at": self.meta.get("started_at"),
            "finished_at": finished.isoformat(),
            "accounts_file": "accounts.sso.txt",
            "accounts_jsonl": "accounts.jsonl",
            "cpa_dir": "cpa",
            "driver": self.meta.get("driver"),
            "captcha_solver": self.meta.get("captcha_solver"),
            "proxy": self.meta.get("proxy"),
            "note_template": self.meta.get("note_template"),
        }
        if extra:
            summary.update(extra)
        with _lock:
            self.summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            # Patch finished_at into run.json
            try:
                meta = dict(self.meta)
                meta["finished_at"] = finished.isoformat()
                meta["summary"] = {
                    "succeeded": summary["succeeded"],
                    "failed": summary["failed"],
                }
                self.run_json_path.write_text(
                    json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                self.meta = meta
            except Exception:
                pass
        return self.summary_path


def get_active_run() -> Optional[RunOutput]:
    return _active


def begin_run(
    cfg: dict,
    *,
    log_callback=None,
    cli_args: Optional[list] = None,
    note_template: str = "",
) -> RunOutput:
    """Create output/runs/<run_id>/ and set it as the active run."""
    global _active
    root = resolve_output_root(cfg)
    root.mkdir(parents=True, exist_ok=True)
    # Ensure standard tree exists even on fresh clones
    (REPO_ROOT / "output").mkdir(parents=True, exist_ok=True)
    (REPO_ROOT / "output" / "runs").mkdir(parents=True, exist_ok=True)

    run_id = build_run_id(cfg)
    run_dir = root / run_id
    # Collision (same second + tags): add counter
    if run_dir.exists():
        n = 2
        while (root / f"{run_id}__{n}").exists():
            n += 1
        run_id = f"{run_id}__{n}"
        run_dir = root / run_id

    cpa_dir = run_dir / "cpa"
    cpa_dir.mkdir(parents=True, exist_ok=True)

    driver = describe_driver(cfg)
    solver = describe_solver(cfg)
    proxy_tag, proxy_info = describe_proxy(cfg)
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()

    meta = {
        "run_id": run_id,
        "started_at": started,
        "finished_at": None,
        "driver": driver,
        "captcha_solver": solver,
        "proxy": proxy_info,
        "proxy_tag": proxy_tag,
        "email_provider": str(cfg.get("email_provider") or ""),
        "domain": str(cfg.get("defaultDomains") or ""),
        "targets": {
            "register_count": int(cfg.get("register_count") or 0),
            "success_target": int(cfg.get("register_success_target") or 0),
        },
        "cpa_auto_add": bool(cfg.get("cpa_auto_add", False)),
        "cpa_auth_dir_config": str(cfg.get("cpa_auth_dir") or ""),
        "cpa_mirror_dir": str(cfg.get("cpa_mirror_dir") or ""),
        "cpa_dir": str(cpa_dir),
        "cli": list(cli_args or []),
        "note_template": note_template or "",
        "paths": {
            "run_dir": str(run_dir),
            "accounts_sso": "accounts.sso.txt",
            "accounts_jsonl": "accounts.jsonl",
            "mail_credentials": "mail_credentials.txt",
            "register_log": "register.log",
            "cpa": "cpa",
            "summary": "summary.json",
        },
    }

    run = RunOutput(run_dir, meta)
    # Touch empty artifacts so layout is obvious mid-run
    for path in (
        run.accounts_sso_path,
        run.accounts_jsonl_path,
        run.mail_credentials_path,
        run.failed_path,
        run.register_log_path,
    ):
        path.touch(exist_ok=True)

    run.run_json_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    # latest symlink for convenience (best-effort; skip on Windows if needed)
    try:
        latest = root / "latest"
        if latest.is_symlink() or latest.exists():
            try:
                latest.unlink()
            except Exception:
                pass
        latest.symlink_to(run_dir.name, target_is_directory=True)
    except Exception:
        pass

    with _lock:
        _active = run

    if log_callback:
        log_callback(f"[*] Run output: {run_dir}")
        log_callback(
            f"[*]   driver={driver} solver={solver} proxy={proxy_tag} "
            f"cpa={cpa_dir.name}/"
        )
    return run


def end_run(
    *,
    succeeded: int = 0,
    failed: int = 0,
    attempted_slots: int | None = None,
    log_callback=None,
    extra: Optional[dict] = None,
) -> Optional[RunOutput]:
    """Write summary.json and clear active run."""
    global _active
    with _lock:
        run = _active
        _active = None
    if run is None:
        return None
    path = run.write_summary(
        succeeded=succeeded,
        failed=failed,
        attempted_slots=attempted_slots,
        extra=extra,
    )
    if log_callback:
        log_callback(f"[*] Run summary: {path}")
        log_callback(
            f"[*]   succeeded={succeeded} failed={failed} dir={run.run_dir}"
        )
    return run


def cpa_write_dirs(cfg: dict) -> list[Path]:
    """Directories that should receive xai-*.json for this success.

    Always includes active run's cpa/ when a run is open.
    Also includes config cpa_auth_dir and cpa_mirror_dir when set.
    """
    dirs: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path | str | None):
        if not p:
            return
        path = Path(p).expanduser()
        key = str(path.resolve()) if path.exists() else str(path)
        if key in seen:
            return
        seen.add(key)
        dirs.append(path)

    run = get_active_run()
    if run is not None:
        _add(run.cpa_dir)

    auth = str(cfg.get("cpa_auth_dir") or "").strip()
    if auth:
        _add(auth)

    mirror = str(cfg.get("cpa_mirror_dir") or "").strip()
    if mirror:
        _add(mirror)

    return dirs
