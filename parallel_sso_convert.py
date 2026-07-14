#!/usr/bin/env python3
"""
Run SSO -> CPA JSON conversion across a fixed set of local HTTP proxies.

This is additive to sso_to_auth_json.py. The original single-file/single-proxy
CLI remains unchanged.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import queue
import secrets
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sso_to_auth_json import sso_to_token, token_to_cpa_record, write_cpa_auth


@dataclass(frozen=True)
class SSOEntry:
    line_no: int
    raw: str
    sso: str


@dataclass
class WorkerResult:
    proxy: str
    port: int
    total: int
    ok: int
    failed: list[SSOEntry]


@dataclass
class ProxyStats:
    port: int
    attempts: int = 0
    ok: int = 0
    failed_attempts: int = 0


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def load_entries(path: Path) -> list[SSOEntry]:
    entries: list[SSOEntry] = []
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        raw = raw_line.strip()
        if not raw or raw.startswith("#"):
            continue
        sso = raw.split("----")[-1].strip() if "----" in raw else raw
        if sso:
            entries.append(SSOEntry(line_no=line_no, raw=raw, sso=sso))
    return entries


def parse_proxy_ports(args) -> list[int]:
    if args.proxy_ports:
        ports: list[int] = []
        for part in args.proxy_ports.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start, end = [int(v.strip()) for v in part.split("-", 1)]
                if end < start:
                    raise ValueError(f"invalid proxy port range: {part}")
                ports.extend(range(start, end + 1))
            else:
                ports.append(int(part))
        return sorted(dict.fromkeys(ports))
    if args.proxy_end < args.proxy_start:
        raise ValueError("--proxy-end must be >= --proxy-start")
    return list(range(args.proxy_start, args.proxy_end + 1))


def split_round_robin(entries: list[SSOEntry], ports: list[int]) -> dict[int, list[SSOEntry]]:
    chunks = {port: [] for port in ports}
    for idx, entry in enumerate(entries):
        chunks[ports[idx % len(ports)]].append(entry)
    return chunks


def append_log(path: Path, message: str, lock: threading.Lock | None = None) -> None:
    line = f"{datetime.now(timezone.utc).isoformat()} {message}\n"
    if lock:
        with lock:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        return
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line)


def convert_entry(
    port: int,
    entry: SSOEntry,
    output_dir: Path,
    log_dir: Path,
    retries: int,
    retry_delay: float,
    prefix: str,
) -> bool:
    proxy = f"http://127.0.0.1:{port}"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"proxy_{port}.log"

    def log(message: str) -> None:
        append_log(log_path, message)

    log(f"{prefix} line={entry.line_no} begin")
    # sso_to_token is upstream auth-code flow (no retries/relay kwargs).
    # Keep batch resilience here with local bounded retries around that call.
    attempts = max(0, int(retries)) + 1
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            token = sso_to_token(
                entry.sso,
                proxy=proxy,
                log=log,
            )
            if token:
                record = token_to_cpa_record(token)
                path = write_cpa_auth(output_dir, record)
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
                log(f"{prefix} line={entry.line_no} wrote={path.name}")
                return True
            log(f"{prefix} line={entry.line_no} attempt={attempt}/{attempts} no token")
        except Exception as exc:
            last_exc = exc
            log(
                f"{prefix} line={entry.line_no} attempt={attempt}/{attempts} "
                f"exception={type(exc).__name__}: {exc}"
            )
        if attempt < attempts:
            delay = float(retry_delay) * (2 ** (attempt - 1))
            log(f"{prefix} line={entry.line_no} retry in {delay:.1f}s")
            time.sleep(delay)

    if last_exc is not None:
        log(f"{prefix} line={entry.line_no} failed after retries: {last_exc}")
    else:
        log(f"{prefix} line={entry.line_no} failed")
    return False


def worker(
    port: int,
    entries: list[SSOEntry],
    output_dir: Path,
    log_dir: Path,
    retries: int,
    retry_delay: float,
    delay: float,
) -> WorkerResult:
    proxy = f"http://127.0.0.1:{port}"
    output_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"proxy_{port}.log"
    failed: list[SSOEntry] = []
    ok = 0

    def log(message: str) -> None:
        append_log(log_path, message)

    log(f"START proxy={proxy} total={len(entries)} output={output_dir}")
    for offset, entry in enumerate(entries, 1):
        success = convert_entry(
            port,
            entry,
            output_dir,
            log_dir,
            retries,
            retry_delay,
            f"[{offset}/{len(entries)}]",
        )
        if success:
            ok += 1
        else:
            failed.append(entry)

        if delay > 0 and offset < len(entries):
            time.sleep(delay)

    log(f"DONE ok={ok} failed={len(failed)} total={len(entries)}")
    return WorkerResult(proxy=proxy, port=port, total=len(entries), ok=ok, failed=failed)


def dynamic_worker(
    port: int,
    pending: "queue.Queue[SSOEntry]",
    ports: list[int],
    output_dir: Path,
    log_dir: Path,
    retries: int,
    retry_delay: float,
    delay: float,
    max_attempts: int,
    lock: threading.Lock,
    attempts_by_sso: dict[str, set[int]],
    entry_by_sso: dict[str, SSOEntry],
    done_sso: set[str],
    final_failed_sso: set[str],
    stats: dict[int, ProxyStats],
    inflight: dict[str, int],
) -> None:
    log_path = log_dir / f"proxy_{port}.log"
    append_log(log_path, f"START dynamic proxy=http://127.0.0.1:{port} output={output_dir}")
    port_count = len(ports)
    max_attempts = max(1, min(max_attempts or port_count, port_count))

    while True:
        try:
            entry = pending.get(timeout=1)
        except queue.Empty:
            with lock:
                if pending.empty() and inflight["count"] == 0:
                    break
            continue

        should_attempt = False
        attempt_no = 0
        with lock:
            if entry.sso in done_sso or entry.sso in final_failed_sso:
                pending.task_done()
                continue

            tried = attempts_by_sso.setdefault(entry.sso, set())
            if len(tried) >= max_attempts:
                final_failed_sso.add(entry.sso)
                pending.task_done()
                continue

            if port in tried:
                pending.put(entry)
                pending.task_done()
                time.sleep(0.05)
                continue

            tried.add(port)
            attempt_no = len(tried)
            stats[port].attempts += 1
            inflight["count"] += 1
            should_attempt = True

        if not should_attempt:
            pending.task_done()
            continue

        success = convert_entry(
            port,
            entry,
            output_dir,
            log_dir,
            retries,
            retry_delay,
            f"[attempt {attempt_no}/{max_attempts}]",
        )

        with lock:
            inflight["count"] -= 1
            if success:
                done_sso.add(entry.sso)
                stats[port].ok += 1
            else:
                stats[port].failed_attempts += 1
                if len(attempts_by_sso.get(entry.sso, set())) >= max_attempts:
                    final_failed_sso.add(entry.sso)
                else:
                    pending.put(entry)

        pending.task_done()
        if delay > 0:
            time.sleep(delay)

    append_log(
        log_path,
        f"DONE dynamic attempts={stats[port].attempts} ok={stats[port].ok} "
        f"failed_attempts={stats[port].failed_attempts}",
    )


def run_retry_across_proxies(
    entries: list[SSOEntry],
    ports: list[int],
    workers: int,
    auth_dir: Path,
    log_dir: Path,
    retries: int,
    retry_delay: float,
    delay: float,
    max_attempts: int,
) -> tuple[list[WorkerResult], list[SSOEntry]]:
    selected_ports = ports[:workers]
    pending: queue.Queue[SSOEntry] = queue.Queue()
    entry_by_sso = {entry.sso: entry for entry in entries}
    for entry in entries:
        pending.put(entry)

    lock = threading.Lock()
    attempts_by_sso: dict[str, set[int]] = {}
    done_sso: set[str] = set()
    final_failed_sso: set[str] = set()
    stats = {port: ProxyStats(port=port) for port in selected_ports}
    inflight = {"count": 0}

    threads = [
        threading.Thread(
            target=dynamic_worker,
            args=(
                port,
                pending,
                selected_ports,
                auth_dir,
                log_dir,
                retries,
                retry_delay,
                delay,
                max_attempts,
                lock,
                attempts_by_sso,
                entry_by_sso,
                done_sso,
                final_failed_sso,
                stats,
                inflight,
            ),
            daemon=False,
        )
        for port in selected_ports
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    failed = [entry for entry in entries if entry.sso in final_failed_sso and entry.sso not in done_sso]
    results = [
        WorkerResult(
            proxy=f"http://127.0.0.1:{port}",
            port=port,
            total=stats[port].attempts,
            ok=stats[port].ok,
            failed=[],
        )
        for port in selected_ports
    ]
    return results, failed


def write_failed_file(fail_dir: Path, failed: list[SSOEntry]) -> Path | None:
    if not failed:
        return None
    fail_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    rows: list[str] = []
    for entry in failed:
        key = entry.sso
        if key in seen:
            continue
        seen.add(key)
        rows.append(entry.raw)
    path = fail_dir / f"failed_sso_{utc_stamp()}_{secrets.token_hex(3)}.txt"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text("\n".join(rows) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description="Parallel SSO -> CPA JSON converter using fixed local proxies")
    ap.add_argument("--sso", required=True, help="SSO list file")
    ap.add_argument("--output-root", default="", help="Output root; default grok2api/cpa_export_proxy_parallel_<timestamp>")
    ap.add_argument("--proxy-start", type=int, default=8021, help="First local proxy port")
    ap.add_argument("--proxy-end", type=int, default=8040, help="Last local proxy port")
    ap.add_argument("--proxy-ports", default="", help="Comma/range ports, e.g. 8021-8040 or 8021,8024")
    ap.add_argument("--workers", type=int, default=0, help="Max parallel workers; default number of proxies")
    ap.add_argument(
        "--retries",
        type=int,
        default=3,
        help="Extra local retries around sso_to_token (upstream auth-code has no built-in retries)",
    )
    ap.add_argument(
        "--retry-delay",
        type=float,
        default=1.0,
        help="Initial local retry delay seconds (exponential backoff)",
    )
    ap.add_argument("--delay", type=float, default=0.0, help="Delay between tokens inside each proxy worker")
    ap.add_argument(
        "--retry-across-proxies",
        action="store_true",
        help="On SSO failure, requeue it for another selected proxy before writing final failed list",
    )
    ap.add_argument(
        "--max-proxy-attempts",
        type=int,
        default=0,
        help="Max different proxies per SSO in --retry-across-proxies mode; default all selected proxies",
    )
    args = ap.parse_args()

    sso_path = Path(args.sso)
    if not sso_path.exists():
        ap.error(f"SSO file not found: {sso_path}")
    if args.retries < 0:
        ap.error("--retries cannot be negative")
    if args.retry_delay < 0:
        ap.error("--retry-delay cannot be negative")
    if args.delay < 0:
        ap.error("--delay cannot be negative")
    if args.max_proxy_attempts < 0:
        ap.error("--max-proxy-attempts cannot be negative")

    try:
        ports = parse_proxy_ports(args)
    except ValueError as exc:
        ap.error(str(exc))
    if not ports:
        ap.error("no proxy ports selected")

    entries = load_entries(sso_path)
    if not entries:
        ap.error("no SSO entries found")

    stamp = utc_stamp()
    output_root = Path(args.output_root) if args.output_root else Path("grok2api") / f"cpa_export_proxy_parallel_{stamp}"
    auth_dir = output_root / "auths"
    log_dir = output_root / "logs"
    fail_dir = output_root / "failed"
    output_root.mkdir(parents=True, exist_ok=True)
    auth_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    fail_dir.mkdir(parents=True, exist_ok=True)
    try:
        output_root.chmod(0o700)
        auth_dir.chmod(0o700)
        log_dir.chmod(0o700)
        fail_dir.chmod(0o700)
    except OSError:
        pass

    chunks = split_round_robin(entries, ports)
    workers = args.workers or len(ports)
    workers = max(1, min(workers, len(ports)))

    print(f"SSO entries: {len(entries)}")
    print(f"Proxies: {len(ports)} ports {ports[0]}-{ports[-1] if len(ports) > 1 else ports[0]}")
    print(f"Workers: {workers}")
    print(f"Retry across proxies: {'yes' if args.retry_across_proxies else 'no'}")
    print(f"Output: {output_root}")
    print(f"Auth JSON dir: {auth_dir}")
    print(f"Logs: {log_dir}")

    results: list[WorkerResult] = []
    failed: list[SSOEntry] = []
    if args.retry_across_proxies:
        results, failed = run_retry_across_proxies(
            entries,
            ports,
            workers,
            auth_dir,
            log_dir,
            args.retries,
            args.retry_delay,
            args.delay,
            args.max_proxy_attempts,
        )
        for result in results:
            print(
                f"proxy {result.port}: ok={result.ok} "
                f"failed_attempts={result.total - result.ok} attempts={result.total}"
            )
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(
                    worker,
                    port,
                    chunk,
                    auth_dir,
                    log_dir,
                    args.retries,
                    args.retry_delay,
                    args.delay,
                ): port
                for port, chunk in chunks.items()
                if chunk
            }
            for future in concurrent.futures.as_completed(future_map):
                port = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:
                    print(f"proxy {port}: worker crashed: {type(exc).__name__}: {exc}")
                    continue
                results.append(result)
                failed.extend(result.failed)
                print(f"proxy {port}: ok={result.ok} failed={len(result.failed)} total={result.total}")

    failed_path = write_failed_file(fail_dir, failed)
    ok = sum(result.ok for result in results)
    total = len(entries) if args.retry_across_proxies else sum(result.total for result in results)
    fail_count = len({entry.sso for entry in failed})
    print(f"Summary: ok={ok} failed={fail_count} total={total}")
    if failed_path:
        print(f"Failed SSO: {failed_path}")
    else:
        print("Failed SSO: none")
    return 0 if ok == total and total == len(entries) else 1


if __name__ == "__main__":
    sys.exit(main())
