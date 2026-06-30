#!/usr/bin/env python3
"""Linux-specific ingest helpers for multi-magazine offload tuning."""
from __future__ import annotations

import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_THROUGHPUT_WORKERS = (1, 2, 4, 6, 8, 12)
DEFAULT_BYTES_PER_WORKER = 1 * 1024 * 1024 * 1024


@dataclass(frozen=True)
class ThroughputResult:
    workers: int
    bytes_written: int
    elapsed_seconds: float
    bytes_per_second: float
    files_written: int
    error: str = ""

    @property
    def mib_per_second(self) -> float:
        return self.bytes_per_second / (1024 * 1024)

    @property
    def gib_per_second(self) -> float:
        return self.bytes_per_second / (1024 * 1024 * 1024)


def parse_worker_counts(text: str | Iterable[int] | None) -> list[int]:
    """Parse a UI worker-count field into sorted unique positive integers."""
    if text is None:
        values = list(DEFAULT_THROUGHPUT_WORKERS)
    elif isinstance(text, str):
        values = []
        for token in text.replace(";", ",").split(","):
            token = token.strip()
            if not token:
                continue
            values.append(int(token))
    else:
        values = [int(v) for v in text]
    cleaned = sorted({max(1, int(v)) for v in values})
    return cleaned or list(DEFAULT_THROUGHPUT_WORKERS)


def recommend_worker_count(results: Iterable[ThroughputResult], *, peak_fraction: float = 0.90) -> int:
    """Return the smallest worker count that reaches a useful fraction of peak throughput."""
    clean = [r for r in results if not r.error and r.bytes_per_second > 0]
    if not clean:
        return 1
    peak = max(r.bytes_per_second for r in clean)
    target = peak * max(0.1, min(1.0, float(peak_fraction)))
    for result in sorted(clean, key=lambda r: r.workers):
        if result.bytes_per_second >= target:
            return result.workers
    return max(clean, key=lambda r: r.bytes_per_second).workers


def destination_profile_key(destination: Path | str) -> str:
    """Return a stable local key for a destination profile."""
    path = Path(destination).expanduser()
    try:
        return str(path.resolve(strict=False))
    except Exception:
        return str(path.absolute())


def throughput_results_as_dicts(results: Iterable[ThroughputResult]) -> list[dict]:
    rows = []
    for result in results:
        rows.append({
            "workers": int(result.workers),
            "bytes_written": int(result.bytes_written),
            "elapsed_seconds": round(float(result.elapsed_seconds), 3),
            "bytes_per_second": float(result.bytes_per_second),
            "files_written": int(result.files_written),
            "error": str(result.error or ""),
        })
    return rows


def build_destination_profile(
    destination: Path | str,
    results: Iterable[ThroughputResult],
    *,
    recommended_workers: int | None = None,
    threads_per_magazine: int = 1,
    throughput_gib_per_worker: float = 1.0,
    worker_counts: Iterable[int] | None = None,
) -> dict:
    """Build a persisted profile from a destination throughput test."""
    destination = Path(destination).expanduser()
    result_list = list(results)
    good = [r for r in result_list if not r.error and r.bytes_per_second > 0]
    best = max(good, key=lambda r: r.bytes_per_second) if good else None
    rec = int(recommended_workers if recommended_workers is not None else recommend_worker_count(good))
    key = destination_profile_key(destination)
    return {
        "version": 1,
        "key": key,
        "path": key,
        "label": destination.name or key,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "max_simultaneous_magazines": max(1, min(24, rec)),
        "threads_per_magazine": max(1, min(8, int(threads_per_magazine or 1))),
        "throughput_gib_per_worker": max(0.1, min(64.0, float(throughput_gib_per_worker or 1.0))),
        "throughput_worker_counts": ",".join(str(v) for v in parse_worker_counts(worker_counts)),
        "best_workers": int(best.workers) if best else 0,
        "peak_bytes_per_second": float(best.bytes_per_second) if best else 0.0,
        "results": throughput_results_as_dicts(result_list),
    }


def profile_for_destination(cfg: dict, destination: Path | str) -> dict | None:
    profiles = cfg.get("linux_destination_profiles") if isinstance(cfg, dict) else {}
    if not isinstance(profiles, dict):
        return None
    key = destination_profile_key(destination)
    profile = profiles.get(key)
    return dict(profile) if isinstance(profile, dict) else None


def derive_ingest_settings_for_destinations(cfg: dict, destinations: Iterable[Path | str]) -> tuple[int, int, list[dict]]:
    """Derive conservative ingest settings from destination profiles.

    When multiple destinations are selected, the slowest profiled destination
    should cap magazine concurrency. Missing profiles fall back to the global
    Linux Ingest defaults.
    """
    cfg = dict(cfg or {})
    fallback_magazines = max(1, min(24, int(cfg.get("linux_max_simultaneous_magazines") or 6)))
    fallback_threads = max(1, min(8, int(cfg.get("linux_threads_per_magazine") or 1)))
    matched: list[dict] = []
    magazine_limits: list[int] = []
    thread_limits: list[int] = []
    for destination in destinations:
        profile = profile_for_destination(cfg, destination)
        if not profile:
            continue
        matched.append(profile)
        try:
            magazine_limits.append(max(1, min(24, int(profile.get("max_simultaneous_magazines") or fallback_magazines))))
        except (TypeError, ValueError):
            pass
        try:
            thread_limits.append(max(1, min(8, int(profile.get("threads_per_magazine") or fallback_threads))))
        except (TypeError, ValueError):
            pass
    max_magazines = min(magazine_limits) if magazine_limits else fallback_magazines
    threads_per_magazine = min(thread_limits) if thread_limits else fallback_threads
    return max_magazines, threads_per_magazine, matched


def _write_test_file(
    path: Path,
    byte_count: int,
    *,
    chunk_size: int,
    cancel_check: Callable[[], bool] | None = None,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    remaining = int(byte_count)
    written = 0
    # A per-file random block prevents sparse-file fast paths without spending
    # CPU generating random data for every byte.
    block = os.urandom(max(1024 * 1024, min(int(chunk_size), 8 * 1024 * 1024)))
    with path.open("wb") as handle:
        try:
            while remaining > 0:
                if cancel_check and cancel_check():
                    raise RuntimeError("cancelled")
                chunk = block if remaining >= len(block) else block[:remaining]
                handle.write(chunk)
                written += len(chunk)
                remaining -= len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            try:
                handle.flush()
                os.fsync(handle.fileno())
            except Exception:
                pass
            raise
    return written


def run_destination_throughput_test(
    destination: Path,
    *,
    worker_counts: str | Iterable[int] | None = None,
    bytes_per_worker: int = DEFAULT_BYTES_PER_WORKER,
    chunk_size: int = 8 * 1024 * 1024,
    progress_callback: Callable[[ThroughputResult], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> list[ThroughputResult]:
    """Measure committed destination write throughput with concurrent streams.

    The test writes temporary files under .mediarunner_throughput_test, fsyncs
    each file, records wall-clock aggregate throughput, then removes the test
    directory. Source media is never read or modified.
    """
    destination = Path(destination).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    counts = parse_worker_counts(worker_counts)
    bytes_per_worker = max(16 * 1024 * 1024, int(bytes_per_worker))
    chunk_size = max(1024 * 1024, int(chunk_size))
    test_root = destination / ".mediarunner_throughput_test" / time.strftime("%Y%m%d_%H%M%S")
    results: list[ThroughputResult] = []

    try:
        for workers in counts:
            if cancel_check and cancel_check():
                break
            stage_dir = test_root / f"{workers:02d}_workers"
            required = int(bytes_per_worker * workers * 1.05)
            free = shutil.disk_usage(destination).free
            if free < required:
                result = ThroughputResult(
                    workers=workers,
                    bytes_written=0,
                    elapsed_seconds=0.0,
                    bytes_per_second=0.0,
                    files_written=0,
                    error=f"insufficient free space: need {required} bytes, have {free} bytes",
                )
                results.append(result)
                if progress_callback:
                    progress_callback(result)
                continue

            if status_callback:
                status_callback(f"Testing {workers} concurrent stream(s)")
            stage_dir.mkdir(parents=True, exist_ok=True)
            started = time.perf_counter()
            bytes_written = 0
            files_written = 0
            error = ""
            try:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [
                        pool.submit(
                            _write_test_file,
                            stage_dir / f"stream_{idx + 1:02d}.bin",
                            bytes_per_worker,
                            chunk_size=chunk_size,
                            cancel_check=cancel_check,
                        )
                        for idx in range(workers)
                    ]
                    for future in as_completed(futures):
                        bytes_written += int(future.result())
                        files_written += 1
            except Exception as exc:
                error = str(exc)
            elapsed = max(0.001, time.perf_counter() - started)
            result = ThroughputResult(
                workers=workers,
                bytes_written=bytes_written,
                elapsed_seconds=elapsed,
                bytes_per_second=(bytes_written / elapsed) if not error else 0.0,
                files_written=files_written,
                error=error,
            )
            results.append(result)
            if progress_callback:
                progress_callback(result)
            shutil.rmtree(stage_dir, ignore_errors=True)
    finally:
        shutil.rmtree(test_root, ignore_errors=True)
        try:
            test_root.parent.rmdir()
        except OSError:
            pass
    return results


def looks_like_media_source(path: Path, *, max_depth: int = 4, max_entries: int = 4000) -> bool:
    """Return True when a folder looks like a mounted camera magazine."""
    root = Path(path).expanduser()
    if not root.is_dir():
        return False
    seen = 0
    root_depth = len(root.parts)
    try:
        for current, dirs, files in os.walk(root):
            cur = Path(current)
            depth = len(cur.parts) - root_depth
            dirs[:] = [
                d for d in dirs
                if not d.startswith(".") and d not in {"_checksums", "_manifests", "__pycache__"}
            ]
            if any(d.lower().endswith((".rdm", ".rdc")) for d in dirs):
                return True
            if any(f.lower().endswith(".r3d") for f in files):
                return True
            seen += len(dirs) + len(files)
            if depth >= max_depth:
                dirs[:] = []
            if seen >= max_entries:
                break
    except OSError:
        return False
    return False


def discover_mounted_magazines(search_roots: Iterable[Path] | None = None) -> list[Path]:
    """Find likely mounted RED/Komodo media folders on Linux-style mount roots."""
    if search_roots is None:
        user = os.environ.get("USER") or ""
        roots = [
            Path("/media") / user if user else Path("/media"),
            Path("/run/media") / user if user else Path("/run/media"),
            Path("/mnt"),
            Path("/Volumes"),
        ]
    else:
        roots = [Path(p).expanduser() for p in search_roots]

    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        candidates = [root]
        try:
            candidates.extend([p for p in root.iterdir() if p.is_dir()])
        except OSError:
            continue
        for candidate in candidates:
            try:
                resolved = str(candidate.resolve())
            except OSError:
                resolved = str(candidate)
            if resolved in seen:
                continue
            if looks_like_media_source(candidate):
                found.append(candidate)
                seen.add(resolved)
    return sorted(found, key=lambda p: str(p).lower())
