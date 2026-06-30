#!/usr/bin/env python3
"""MediaRunner field-stress harness (--profile stress-field).

Proves the resilience layer automatically — the failure modes that previously
required pulling cables on set:

  ftp_drop_resume      connection dies mid-file → retry, reconnect, REST-resume
  ftp_reject_rest      server refuses REST → clean full restart, still verifies
  ftp_stall_timeout    server hangs past the client timeout → timeout, retry
  kill_mid_transfer    SIGKILL the app mid-copy (×N) → no corrupt committed
                       files ever; rerun completes and verifies everything
  enospc_abort         disk fills mid-write → FatalTransferError raised, no
                       partial file committed, nothing retried
  cancel_fuzzer        cancel at random moments (×N) → only Verified or
                       Cancelled outcomes; every committed file is intact

Real-camera behavior and physical drive unplugs remain on the manual matrix.
"""
from __future__ import annotations

import csv
import os
import random
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediarunner_core import (  # noqa: E402
    FatalTransferError,
    Manifest,
    TransferCancelledError,
    compute_checksums,
)
from mediarunner_transfer import discover_files, transfer_file  # noqa: E402

KILL_ITERATIONS = int(os.environ.get("MEDIARUNNER_STRESS_KILL_RUNS", "5"))
FUZZ_ITERATIONS = int(os.environ.get("MEDIARUNNER_STRESS_FUZZ_RUNS", "12"))
FTP_USER = "ftp1"
FTP_PASS = "12345678"


def _xx(path: Path) -> str:
    return compute_checksums(path, algorithms=("xxh128",))["xxh128"]


def _make_red_media_tree(root: Path, *, files: int = 6, size: int = 2_000_000, seed: int = 99) -> dict[str, str]:
    """Create /media/R007.RDM/<clip>.RDC/<clip>.R3D files; return name → hash."""
    rng = random.Random(seed)
    hashes: dict[str, str] = {}
    for i in range(files):
        clip = f"A007_C{i:03d}"
        d = root / "media" / "R007.RDM" / f"{clip}.RDC"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{clip}_001.R3D"
        f.write_bytes(rng.randbytes(size))
        hashes[f.name] = _xx(f)
    return hashes


def _make_local_tree(root: Path, *, files: int, size: int, seed: int) -> dict[str, str]:
    rng = random.Random(seed)
    hashes: dict[str, str] = {}
    for i in range(files):
        d = root / "CAM_A" / "007.RDM" / f"A007_C{i:03d}.RDC"
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"A007_C{i:03d}_001.R3D"
        f.write_bytes(rng.randbytes(size))
        hashes[str(f.relative_to(root))] = _xx(f)
    return hashes


def _verify_outputs(out_root: Path, expected: dict[str, str]) -> list[str]:
    """Return failure strings for any expected file missing or hash-mismatched."""
    failures = []
    found = {p.name: p for p in out_root.rglob("*.R3D") if p.is_file()}
    for name, want in expected.items():
        got = found.get(Path(name).name)
        if got is None:
            failures.append(f"missing output: {name}")
        elif _xx(got) != want:
            failures.append(f"hash mismatch: {name}")
    return failures


# ── FTP fault scenarios ───────────────────────────────────────────────────────

def _run_ftp_fault(mode: str, work: Path, *, timeout: float, label: str,
                   preseed_parts: bool = False, log=print) -> tuple[bool, str]:
    try:
        from fault_injection_ftp import FaultInjectionFTPServer
    except ImportError:
        try:
            from validation.fault_injection_ftp import FaultInjectionFTPServer
        except ImportError:
            return True, "SKIPPED — pyftpdlib not installed (pip3 install pyftpdlib)"
    from mediarunner_red_wireless import discover_red_wireless_media, run_red_wireless_ingest

    server_root = work / f"{label}_camera"
    out_root = work / f"{label}_out"
    expected = _make_red_media_tree(server_root)

    with FaultInjectionFTPServer(server_root, mode=mode, fail_after_bytes=256 * 1024) as srv:
        if preseed_parts:
            # Simulate a previous interrupted run so REST is exercised even
            # when the server rejects it.
            for d in server_root.rglob("*.R3D"):
                rel = d.relative_to(server_root)
                part = out_root / rel.relative_to("media")
                part = part.with_name(part.name + ".part")
                part.parent.mkdir(parents=True, exist_ok=True)
                part.write_bytes(d.read_bytes()[: 100 * 1024])

        discovery = discover_red_wireless_media(
            host="127.0.0.1", reel="007", clip_spec="ALL",
            username=FTP_USER, password=FTP_PASS, port=srv.port,
            timeout=timeout, use_ftps=False,
        )
        if not discovery.ok:
            return False, f"discovery failed: {discovery.error}"
        result = run_red_wireless_ingest(
            host="127.0.0.1", discovery=discovery,
            destinations=[("Primary", out_root, 0)],
            username=FTP_USER, password=FTP_PASS, port=srv.port,
            timeout=timeout, use_ftps=False,
            verify=True, second_pass=False,
            log_callback=lambda m: log(f"      {m}"),
        )
        faults = srv.plan.total_faults_fired

    failures = _verify_outputs(out_root, expected)
    if result.fail_count or not result.ok:
        failures.append(f"ingest reported {result.fail_count} failure(s)")
    if mode in ("drop", "stall") and faults == 0:
        failures.append("fault server never fired — test proved nothing")
    note = f"{len(expected)} files · {faults} faults injected · all recovered and verified"
    return (not failures), ("; ".join(failures) if failures else note)


# ── Kill test ─────────────────────────────────────────────────────────────────

def run_kill_test(work: Path, log=print) -> tuple[bool, str]:
    src = work / "kill_src"
    expected = _make_local_tree(src, files=24, size=1_200_000, seed=41)
    failures: list[str] = []
    kills = 0

    for it in range(KILL_ITERATIONS):
        dst = work / f"kill_dst_{it}"
        manifest = dst / "_manifest.csv"
        dst.mkdir(parents=True, exist_ok=True)
        proc = subprocess.Popen(
            [sys.executable, str(Path(__file__).parent / "_kill_target.py"), str(src), str(dst), str(manifest)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "MEDIARUNNER_KILL_THROTTLE": os.environ.get("MEDIARUNNER_KILL_THROTTLE", "0.05")},
        )
        time.sleep(random.uniform(0.25, 1.4))
        if proc.poll() is None:
            proc.send_signal(signal.SIGKILL)
            proc.wait()
            kills += 1
            log(f"      kill #{it + 1}: SIGKILL mid-transfer")
        else:
            log(f"      kill #{it + 1}: finished before kill window (small payload)")

        # Invariant 1: every committed (non-.part) file must be intact.
        for committed in dst.rglob("*.R3D"):
            rel = None
            for key in expected:
                if Path(key).name == committed.name:
                    rel = key
                    break
            if rel is None:
                failures.append(f"iter {it}: unexpected output {committed.name}")
            elif _xx(committed) != expected[rel]:
                failures.append(f"iter {it}: CORRUPT COMMITTED FILE {committed.name}")

        # Invariant 2: manifest stays parseable.
        if manifest.exists():
            try:
                with open(manifest, newline="") as fh:
                    list(csv.DictReader(fh))
            except Exception as exc:
                failures.append(f"iter {it}: manifest unreadable: {exc}")

        # Invariant 3: a rerun must converge to fully verified.
        rerun = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "_kill_target.py"), str(src), str(dst), str(manifest)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300,
            env={**os.environ, "MEDIARUNNER_KILL_THROTTLE": "0"},  # rerun at full speed
        )
        if rerun.returncode != 0:
            failures.append(f"iter {it}: rerun after kill did not verify cleanly")
        else:
            failures.extend(f"iter {it}: {msg}" for msg in _verify_outputs(dst, expected))

    if kills == 0:
        failures.append("no run was actually killed mid-transfer — payload/throttle too small, test proved nothing")
    note = f"{KILL_ITERATIONS} iterations · {kills} SIGKILLs · zero corrupt committed files · rerun converged"
    return (not failures), ("; ".join(failures[:4]) if failures else note)


# ── ENOSPC injection ──────────────────────────────────────────────────────────

class _ENOSPCWriter:
    def __init__(self, handle, fail_after: int):
        self._h = handle
        self._left = fail_after

    def write(self, data):
        if self._left - len(data) < 0:
            raise OSError(28, "No space left on device (injected)")
        self._left -= len(data)
        return self._h.write(data)

    def __getattr__(self, name):
        return getattr(self._h, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._h.close()
        return False


def run_enospc_test(work: Path, log=print) -> tuple[bool, str]:
    src = work / "enospc_src"
    src.mkdir(parents=True, exist_ok=True)
    big = src / "CAM_A" / "big_clip.R3D"
    big.parent.mkdir(parents=True, exist_ok=True)
    big.write_bytes(random.Random(7).randbytes(4_000_000))
    dst_root = work / "enospc_dst"
    manifest = Manifest(work / "enospc_manifest.csv")

    original_open = Path.open

    def patched_open(self, mode="r", *args, **kwargs):
        handle = original_open(self, mode, *args, **kwargs)
        if str(self).endswith(".part") and "w" in str(mode):
            return _ENOSPCWriter(handle, fail_after=1_000_000)
        return handle

    failures = []
    Path.open = patched_open
    try:
        try:
            transfer_file(big, dst_root / "big_clip.R3D", manifest, "CAM_A", "", "", True, threading.Lock())
            failures.append("disk-full did not raise FatalTransferError")
        except FatalTransferError:
            log("      FatalTransferError raised on injected ENOSPC — job-level abort reachable")
        except Exception as exc:
            failures.append(f"wrong exception type for disk-full: {type(exc).__name__}: {exc}")
    finally:
        Path.open = original_open

    if (dst_root / "big_clip.R3D").exists():
        failures.append("partial file was committed despite disk-full")
    return (not failures), ("; ".join(failures) if failures else "ENOSPC → fatal abort, nothing committed, no retries")


# ── Cancellation fuzzer ───────────────────────────────────────────────────────

def run_cancel_fuzzer(work: Path, log=print) -> tuple[bool, str]:
    src = work / "fuzz_src"
    expected = _make_local_tree(src, files=18, size=900_000, seed=17)
    files = discover_files(src, [])
    failures: list[str] = []
    cancelled_runs = 0

    for it in range(FUZZ_ITERATIONS):
        dst_root = work / f"fuzz_dst_{it}"
        manifest = Manifest(dst_root / "_manifest.csv")
        cancel = threading.Event()
        lock = threading.Lock()
        errors: list[str] = []

        def worker(meta):
            f, cam, reel, clip = meta
            try:
                transfer_file(f, dst_root / f.relative_to(src), manifest, cam, reel, clip,
                              True, lock, cancel_check=cancel.is_set)
            except TransferCancelledError:
                pass
            except Exception as exc:  # any other escape is a fuzzer failure
                errors.append(f"{f.name}: {type(exc).__name__}: {exc}")

        threads = [threading.Thread(target=worker, args=(m,), daemon=True) for m in files]
        for t in threads:
            t.start()
        time.sleep(random.uniform(0.0, 0.35))
        cancel.set()
        cancelled_runs += 1
        for t in threads:
            t.join(timeout=60)

        failures.extend(f"iter {it}: {e}" for e in errors)
        # Invariant: anything committed must be byte-perfect.
        for committed in dst_root.rglob("*.R3D"):
            key = next((k for k in expected if Path(k).name == committed.name), None)
            if key is None or _xx(committed) != expected[key]:
                failures.append(f"iter {it}: CORRUPT COMMITTED FILE {committed.name}")

    note = f"{FUZZ_ITERATIONS} random-cancel runs · only Verified/Cancelled outcomes · zero corrupt files"
    return (not failures), ("; ".join(failures[:4]) if failures else note)


# ── Entry point used by run_validation_suite ─────────────────────────────────

def run_all(root: Path, make_result, log=print) -> list:
    """Run every field-stress scenario; returns ScenarioResult-compatible rows."""
    work = Path(root) / "stress_field"
    work.mkdir(parents=True, exist_ok=True)
    results = []

    scenarios = [
        ("ftp_drop_resume", lambda: _run_ftp_fault("drop", work, timeout=6.0, label="drop", log=log)),
        ("ftp_reject_rest", lambda: _run_ftp_fault("reject_rest", work, timeout=6.0, label="rest", preseed_parts=True, log=log)),
        ("ftp_stall_timeout", lambda: _run_ftp_fault("stall", work, timeout=2.0, label="stall", log=log)),
        ("kill_mid_transfer", lambda: run_kill_test(work, log=log)),
        ("enospc_abort", lambda: run_enospc_test(work, log=log)),
        ("cancel_fuzzer", lambda: run_cancel_fuzzer(work, log=log)),
    ]
    for name, fn in scenarios:
        started = time.perf_counter()
        try:
            ok, note = fn()
        except Exception as exc:
            ok, note = False, f"harness error: {type(exc).__name__}: {exc}"
        status = "PASS" if ok else "FAIL"
        if note.startswith("SKIPPED"):
            status = "SKIP"
        results.append(make_result(
            name, status,
            1 if ok else 0, 0 if ok else 1, 0,
            time.perf_counter() - started, "", "",
            note=note,
        ))
    return results


if __name__ == "__main__":
    import tempfile
    from dataclasses import dataclass

    @dataclass
    class _R:
        name: str; status: str; ok_rows: int; fail_rows: int; bytes_copied: int
        seconds: float; manifest: str; report: str; note: str = ""

    base = Path(tempfile.mkdtemp(prefix="mediarunner_stress_field_"))
    rows = run_all(base, _R)
    print()
    for r in rows:
        print(f"{r.status:4}  {r.name:24} {r.note}")
    print(f"\nWork dir: {base}")
    raise SystemExit(0 if all(r.status != "FAIL" for r in rows) else 1)
