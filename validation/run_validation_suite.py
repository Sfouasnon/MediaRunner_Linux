#!/usr/bin/env python3
"""MediaRunner Validation Suite v5.

Self-contained local validation for MediaRunner. It creates deterministic test
media, exercises copy strategies, verifies checksum/report behavior, and can run
quick, extended, or repeated stress profiles.

It does not replace real hardware testing. RED Wireless Ingest, drive removal,
low-space, and long-duration transfers still require the field matrix in
TEST_MATRIX.md.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html
import json
import os
import platform
import shutil
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediarunner_core import Manifest, human_size, xxh128, write_html_report  # noqa: E402
from mediarunner_transfer import copy2_with_progress, discover_files  # noqa: E402


TEST_FILES = (
    ("CAM_A/007.RDM/A007_C060_001.RDC/A007_C060_001.R3D", 1_200_001),
    ("CAM_A/007.RDM/A007_C060_001.RDC/A007_C060_001.RMD", 33_333),
    ("CAM_A/007.RDM/A007_C061_001.RDC/A007_C061_001.R3D", 2_400_017),
    ("CAM_B/007.RDM/B007_C060_001.RDC/B007_C060_001.R3D", 1_700_009),
    ("CAM_B/007.RDM/B007_C064_001.RDC/B007_C064_001.R3D", 4_900_021),
    ("GENERIC_CARD/DCIM/100MEDIA/CLIP_0001.MOV", 777_777),
)

EDGE_FILES = (
    ("CAM C/008.RDM/C008_C001_001.RDC/C008_C001_001 with spaces.R3D", 111_111),
    ("CAM_C/008.RDM/C008_C002_001.RDC/subfolder/deep/path/C008_C002_001.R3D", 222_222),
    ("GENERIC_CARD/DCIM/100MEDIA/Clip_With_#_Symbol_001.MOV", 123_456),
    ("LONG_PATH/" + "/".join(["nested_level_%02d" % i for i in range(12)]) + "/LONG_CLIP_0001.MXF", 333_333),
)

PASS_STATUSES = {"OK", "Verified", "Copied", "Skipped"}
FAIL_STATUSES = {"FAIL", "MISSING", "ERROR"}


@dataclass
class ScenarioResult:
    name: str
    status: str
    ok_rows: int = 0
    fail_rows: int = 0
    bytes_copied: int = 0
    seconds: float = 0.0
    manifest: str = ""
    report: str = ""
    note: str = ""
    display_name: str = ""
    display_ok: object = ""
    display_fail: object = ""
    display_note: str = ""
    expected_corruption_test: bool = False
    files_tested: int = 0
    intentional_corruptions: int = 0
    corruptions_detected: int = 0
    corruptions_missed: int = 0
    detection_rate: float = 0.0
    checks_expected: int = 0
    checks_passed: int = 0
    expected_fault_test: bool = False
    fault_kind: str = ""
    intentional_faults: int = 0
    faults_detected: int = 0
    faults_missed: int = 0


def deterministic_bytes(label: str, size: int, seed: int) -> Iterable[bytes]:
    """Yield deterministic pseudo-random bytes without holding the whole file."""
    counter = 0
    remaining = int(size)
    while remaining > 0:
        block = hashlib.sha256(f"MediaRunner:{seed}:{label}:{counter}".encode("utf-8")).digest()
        chunk = block[: min(len(block), remaining)]
        yield chunk
        remaining -= len(chunk)
        counter += 1


def create_test_media(source_root: Path, *, seed: int = 5700) -> int:
    source_root.mkdir(parents=True, exist_ok=True)
    total = 0
    for relative, size in TEST_FILES:
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            for chunk in deterministic_bytes(relative, size, seed):
                handle.write(chunk)
        total += int(size)
    return total




def create_edge_media(source_root: Path, *, seed: int = 8642) -> int:
    source_root.mkdir(parents=True, exist_ok=True)
    total = 0
    for relative, size in EDGE_FILES:
        path = source_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            for chunk in deterministic_bytes(relative, size, seed):
                handle.write(chunk)
        total += int(size)
    return total

def manifest_counts(manifest_path: Path) -> tuple[int, int, list[dict[str, str]]]:
    rows: list[dict[str, str]] = []
    with manifest_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    ok = sum(1 for row in rows if row.get("status") in PASS_STATUSES)
    fail = sum(1 for row in rows if row.get("status") in FAIL_STATUSES)
    return ok, fail, rows


def _copy_one(
    *,
    src: Path,
    dst: Path,
    source_root: Path,
    upstream_label: str,
    downstream_label: str,
    manifest: Manifest,
    lock: threading.Lock,
    verify_inline: bool,
    progress_callback=None,
    allow_skip: bool = True,
) -> tuple[bool, int, str]:
    rel = src.relative_to(source_root)
    camera = rel.parts[0] if len(rel.parts) >= 1 else ""
    reel = rel.parts[1] if len(rel.parts) >= 2 else ""
    clip = rel.parts[2] if len(rel.parts) >= 3 else ""
    size = src.stat().st_size
    method = f"{upstream_label} -> {downstream_label}"

    if allow_skip and dst.exists():
        src_hash = xxh128(src)
        dst_hash = xxh128(dst)
        if src_hash == dst_hash:
            with lock:
                manifest.write(
                    method=method,
                    source_path=str(src),
                    destination_path=str(dst),
                    camera=camera,
                    reel=reel,
                    clip=clip,
                    file=src.name,
                    size_bytes=size,
                    size_human=human_size(size),
                    src_hash=src_hash,
                    dst_hash=dst_hash,
                    status="Skipped",
                    note="Already verified",
                )
            return True, 0, "Skipped"

    copy2_with_progress(src, dst, progress_callback=progress_callback)
    if verify_inline:
        src_hash = xxh128(src)
        dst_hash = xxh128(dst)
        ok = src_hash == dst_hash
        status = "Verified" if ok else "FAIL"
        note = "" if ok else "Hash mismatch"
    else:
        src_hash = ""
        dst_hash = ""
        ok = True
        status = "Copied"
        note = "Verification deferred"
    with lock:
        manifest.write(
            method=method,
            source_path=str(src),
            destination_path=str(dst),
            camera=camera,
            reel=reel,
            clip=clip,
            file=src.name,
            size_bytes=size,
            size_human=human_size(size),
            src_hash=src_hash,
            dst_hash=dst_hash,
            status=status,
            note=note,
        )
    return ok, size, status


def verify_leg(
    *,
    source_root: Path,
    dest_root: Path,
    upstream_label: str,
    downstream_label: str,
    manifest: Manifest,
    lock: threading.Lock,
) -> tuple[int, int]:
    ok = 0
    fail = 0
    for src, camera, reel, clip in discover_files(source_root, []):
        rel = src.relative_to(source_root)
        dst = dest_root / rel
        size = src.stat().st_size
        method = f"VERIFY {upstream_label} -> {downstream_label}"
        if not dst.exists():
            with lock:
                manifest.write(
                    method=method,
                    source_path=str(src),
                    destination_path=str(dst),
                    camera=camera,
                    reel=reel,
                    clip=clip,
                    file=src.name,
                    size_bytes=size,
                    size_human=human_size(size),
                    status="MISSING",
                    note="Destination file missing",
                )
            fail += 1
            continue
        src_hash = xxh128(src)
        dst_hash = xxh128(dst)
        matched = src_hash == dst_hash
        with lock:
            manifest.write(
                method=method,
                source_path=str(src),
                destination_path=str(dst),
                camera=camera,
                reel=reel,
                clip=clip,
                file=src.name,
                size_bytes=size,
                size_human=human_size(size),
                src_hash=src_hash,
                dst_hash=dst_hash,
                status="Verified" if matched else "FAIL",
                note="" if matched else "Hash mismatch",
            )
        if matched:
            ok += 1
        else:
            fail += 1
    return ok, fail


def copy_leg(
    *,
    source_root: Path,
    dest_root: Path,
    upstream_label: str,
    downstream_label: str,
    manifest: Manifest,
    verify_inline: bool = True,
    workers: int = 2,
    allow_skip: bool = True,
) -> tuple[int, int, int]:
    lock = threading.Lock()
    files = [(src, dest_root / src.relative_to(source_root)) for src, *_ in discover_files(source_root, [])]
    ok = 0
    fail = 0
    bytes_copied = 0
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as pool:
        futures = [
            pool.submit(
                _copy_one,
                src=src,
                dst=dst,
                source_root=source_root,
                upstream_label=upstream_label,
                downstream_label=downstream_label,
                manifest=manifest,
                lock=lock,
                verify_inline=verify_inline,
                allow_skip=allow_skip,
            )
            for src, dst in files
        ]
        for future in as_completed(futures):
            try:
                passed, copied, _status = future.result()
                bytes_copied += int(copied)
                ok += 1 if passed else 0
                fail += 0 if passed else 1
            except Exception as exc:
                fail += 1
                with lock:
                    manifest.write(method=f"{upstream_label} -> {downstream_label}", status="ERROR", note=str(exc))
    return ok, fail, bytes_copied


def write_scenario_report(manifest_path: Path, project: str, source: Path, dest: Path) -> tuple[str, int, int]:
    report_path = manifest_path.with_suffix(".html")
    ok, fail = write_html_report(
        manifest_path,
        project,
        report_path,
        source_path=str(source),
        destination_path=str(dest),
        method_label=project,
    )
    return str(report_path), int(ok), int(fail)


def run_single_destination(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    dest = root / "dest_primary"
    manifest_path = root / "manifests" / "single_destination.csv"
    manifest = Manifest(manifest_path)
    ok, fail, copied = copy_leg(source_root=source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest)
    report, report_ok, report_fail = write_scenario_report(manifest_path, "single_destination", source, dest)
    passed = fail == 0 and report_fail == 0 and ok == len(TEST_FILES)
    return ScenarioResult("single_destination", "PASS" if passed else "FAIL", report_ok, report_fail, copied, time.perf_counter() - started, str(manifest_path), report)


def run_simultaneous(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    manifest_path = root / "manifests" / "simultaneous.csv"
    manifest = Manifest(manifest_path)
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(copy_leg, source_root=source, dest_root=root / "sim_primary", upstream_label="Source", downstream_label="Primary", manifest=manifest, workers=2),
            pool.submit(copy_leg, source_root=source, dest_root=root / "sim_secondary", upstream_label="Source", downstream_label="Secondary", manifest=manifest, workers=2),
        ]
        for future in as_completed(futures):
            results.append(future.result())
    copied = sum(item[2] for item in results)
    report, report_ok, report_fail = write_scenario_report(manifest_path, "simultaneous_source_to_each_destination", source, root / "sim_primary")
    expected_rows = len(TEST_FILES) * 2
    passed = report_fail == 0 and report_ok == expected_rows
    return ScenarioResult("simultaneous", "PASS" if passed else "FAIL", report_ok, report_fail, copied, time.perf_counter() - started, str(manifest_path), report)


def run_primary_first(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    manifest_path = root / "manifests" / "primary_first.csv"
    manifest = Manifest(manifest_path)
    primary = root / "pf_primary"
    secondary = root / "pf_secondary"
    ok1, fail1, copied1 = copy_leg(source_root=source, dest_root=primary, upstream_label="Source", downstream_label="Primary", manifest=manifest)
    copied2 = 0
    fail2 = 0
    if fail1 == 0:
        _ok2, fail2, copied2 = copy_leg(source_root=source, dest_root=secondary, upstream_label="Source", downstream_label="Secondary", manifest=manifest)
    report, report_ok, report_fail = write_scenario_report(manifest_path, "primary_first", source, primary)
    expected_rows = len(TEST_FILES) * 2
    passed = fail1 == 0 and fail2 == 0 and report_fail == 0 and report_ok == expected_rows
    return ScenarioResult("primary_first", "PASS" if passed else "FAIL", report_ok, report_fail, copied1 + copied2, time.perf_counter() - started, str(manifest_path), report)


def run_cascade(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    manifest_path = root / "manifests" / "cascade.csv"
    manifest = Manifest(manifest_path)
    primary = root / "cas_primary"
    secondary = root / "cas_secondary"
    third = root / "cas_third"
    copied = 0
    fail_total = 0
    _ok, fail, bytes_done = copy_leg(source_root=source, dest_root=primary, upstream_label="Source", downstream_label="Primary", manifest=manifest)
    fail_total += fail; copied += bytes_done
    if fail_total == 0:
        _ok, fail, bytes_done = copy_leg(source_root=primary, dest_root=secondary, upstream_label="Primary", downstream_label="Secondary", manifest=manifest)
        fail_total += fail; copied += bytes_done
    if fail_total == 0:
        _ok, fail, bytes_done = copy_leg(source_root=secondary, dest_root=third, upstream_label="Secondary", downstream_label="Third", manifest=manifest)
        fail_total += fail; copied += bytes_done
    report, report_ok, report_fail = write_scenario_report(manifest_path, "cascade_leg_verification", source, third)
    expected_rows = len(TEST_FILES) * 3
    passed = fail_total == 0 and report_fail == 0 and report_ok == expected_rows
    return ScenarioResult("cascade", "PASS" if passed else "FAIL", report_ok, report_fail, copied, time.perf_counter() - started, str(manifest_path), report)


def run_second_pass(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    manifest_path = root / "manifests" / "second_pass.csv"
    manifest = Manifest(manifest_path)
    primary = root / "sp_primary"
    _ok, fail_copy, copied = copy_leg(source_root=source, dest_root=primary, upstream_label="Source", downstream_label="Primary", manifest=manifest, verify_inline=False)
    verify_ok, verify_fail = verify_leg(source_root=source, dest_root=primary, upstream_label="Source", downstream_label="Primary", manifest=manifest, lock=threading.Lock())
    report, report_ok, report_fail = write_scenario_report(manifest_path, "second_pass_checksum", source, primary)
    passed = fail_copy == 0 and verify_fail == 0 and verify_ok == len(TEST_FILES) and report_fail == 0
    return ScenarioResult("second_pass_checksum", "PASS" if passed else "FAIL", report_ok, report_fail, copied, time.perf_counter() - started, str(manifest_path), report)


def run_skip_existing(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    manifest_path = root / "manifests" / "skip_existing.csv"
    manifest = Manifest(manifest_path)
    dest = root / "skip_primary"
    copy_leg(source_root=source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest, allow_skip=True)
    # Second run should skip because hashes match.
    _ok2, fail2, copied2 = copy_leg(source_root=source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest, allow_skip=True)
    report, report_ok, report_fail = write_scenario_report(manifest_path, "skip_existing_verified", source, dest)
    _ok_rows, _fail_rows, rows = manifest_counts(manifest_path)
    skipped = sum(1 for row in rows if row.get("status") == "Skipped")
    passed = fail2 == 0 and copied2 == 0 and skipped == len(TEST_FILES) and report_fail == 0
    return ScenarioResult("skip_existing", "PASS" if passed else "FAIL", report_ok, report_fail, copied2, time.perf_counter() - started, str(manifest_path), report, note=f"skipped={skipped}")


def corrupt_first_file(dest_root: Path) -> Path:
    files = sorted(path for path in dest_root.rglob("*") if path.is_file())
    if not files:
        raise RuntimeError("No destination file available to corrupt")
    target = files[0]
    with target.open("r+b") as handle:
        first = handle.read(1)
        handle.seek(0)
        handle.write(bytes([(first[0] ^ 0xFF) if first else 0xFF]))
    return target


def run_corruption_detection(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    manifest_path = root / "manifests" / "corruption_detection.csv"
    manifest = Manifest(manifest_path)
    dest = root / "corrupt_primary"
    copy_leg(source_root=source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest)
    corrupted = corrupt_first_file(dest)
    _verify_ok, verify_fail = verify_leg(source_root=source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest, lock=threading.Lock())
    report, report_ok, report_fail = write_scenario_report(manifest_path, "corruption_detection", source, dest)

    # This is an intentional negative test: PASS means MediaRunner caught the
    # injected corruption. The manifest/report still contain one FAIL row because
    # that is the evidence of successful detection. The validation UI/report below
    # translates that evidence into user-facing detection language.
    intentional_corruptions = 1
    corruptions_detected = 1 if verify_fail >= intentional_corruptions and report_fail >= intentional_corruptions else 0
    corruptions_missed = intentional_corruptions - corruptions_detected
    passed = corruptions_missed == 0
    detection_rate = 100.0 if intentional_corruptions and passed else 0.0
    checks_expected = report_ok + report_fail
    checks_passed = checks_expected if passed else max(0, checks_expected - corruptions_missed)
    base_note = f"intentional corruption detected and flagged: {corrupted.relative_to(dest)}" if passed else f"intentional corruption was NOT detected: {corrupted.relative_to(dest)}"
    display_note = (
        "MediaRunner intentionally corrupted a file. Testing corruption detection layer. "
        + (
            f"Status: PASS — {checks_passed}/{checks_expected} validation checks passed; "
            "intentional corruption detected and flagged."
            if passed
            else f"Status: FAIL — MediaRunner failed to detect {corruptions_missed}/{intentional_corruptions} "
            "intentionally corrupted file; investigate checksum verification."
        )
    )
    return ScenarioResult(
        "corruption_detection",
        "PASS" if passed else "FAIL",
        report_ok,
        report_fail,
        0,
        time.perf_counter() - started,
        str(manifest_path),
        report,
        note=base_note,
        display_name="Corruption Detection Layer",
        display_ok=checks_passed,
        display_fail=0 if passed else corruptions_missed,
        display_note=display_note,
        expected_corruption_test=True,
        expected_fault_test=True,
        fault_kind="corruption",
        files_tested=len(TEST_FILES),
        intentional_corruptions=intentional_corruptions,
        corruptions_detected=corruptions_detected,
        corruptions_missed=corruptions_missed,
        intentional_faults=intentional_corruptions,
        faults_detected=corruptions_detected,
        faults_missed=corruptions_missed,
        detection_rate=detection_rate,
        checks_expected=checks_expected,
        checks_passed=checks_passed,
    )




def run_empty_folder(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    empty_source = root / "empty_source"
    empty_source.mkdir(parents=True, exist_ok=True)
    dest = root / "empty_dest"
    manifest_path = root / "manifests" / "empty_folder.csv"
    manifest = Manifest(manifest_path)
    ok, fail, copied = copy_leg(source_root=empty_source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest)
    report, report_ok, report_fail = write_scenario_report(manifest_path, "empty_folder", empty_source, dest)
    passed = ok == 0 and fail == 0 and copied == 0 and report_fail == 0
    return ScenarioResult("empty_folder", "PASS" if passed else "FAIL", report_ok, report_fail, copied, time.perf_counter() - started, str(manifest_path), report, note="No files copied; no false success rows")


def run_long_nested_paths(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    edge_source = root / "edge_source"
    edge_bytes = create_edge_media(edge_source)
    dest = root / "edge_dest"
    manifest_path = root / "manifests" / "long_nested_paths.csv"
    manifest = Manifest(manifest_path)
    ok, fail, copied = copy_leg(source_root=edge_source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest)
    report, report_ok, report_fail = write_scenario_report(manifest_path, "long_nested_paths", edge_source, dest)
    passed = fail == 0 and report_fail == 0 and ok == len(EDGE_FILES) and copied == edge_bytes
    return ScenarioResult("long_nested_paths", "PASS" if passed else "FAIL", report_ok, report_fail, copied, time.perf_counter() - started, str(manifest_path), report, note="spaces/symbols/deep folders")


def run_missing_destination_detection(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    dest = root / "missing_dest_primary"
    manifest_path = root / "manifests" / "missing_destination_detection.csv"
    manifest = Manifest(manifest_path)
    copy_leg(source_root=source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest)
    victim = sorted(path for path in dest.rglob("*") if path.is_file())[0]
    victim.unlink()
    _verify_ok, verify_fail = verify_leg(source_root=source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest, lock=threading.Lock())
    report, report_ok, report_fail = write_scenario_report(manifest_path, "missing_destination_detection", source, dest)

    intentional_faults = 1
    faults_detected = 1 if verify_fail >= intentional_faults and report_fail >= intentional_faults else 0
    faults_missed = intentional_faults - faults_detected
    passed = faults_missed == 0
    checks_expected = report_ok + report_fail
    checks_passed = checks_expected if passed else max(0, checks_expected - faults_missed)
    base_note = (
        "missing destination clip detected and flagged: " + str(victim.relative_to(dest))
        if passed
        else "intentionally missing destination clip was NOT detected: " + str(victim.relative_to(dest))
    )
    display_note = (
        "MediaRunner intentionally removed one destination clip. Testing missing destination detection layer. "
        + (
            f"Status: PASS — {checks_passed}/{checks_expected} validation checks passed; "
            "missing destination clip detected and flagged."
            if passed
            else f"Status: FAIL — MediaRunner failed to detect {faults_missed}/{intentional_faults} "
            "intentionally missing destination clip; investigate verification reporting."
        )
    )
    return ScenarioResult(
        "missing_destination_detection",
        "PASS" if passed else "FAIL",
        report_ok,
        report_fail,
        0,
        time.perf_counter() - started,
        str(manifest_path),
        report,
        note=base_note,
        display_name="Missing Destination Detection Layer",
        display_ok=checks_passed,
        display_fail=0 if passed else faults_missed,
        display_note=display_note,
        expected_fault_test=True,
        fault_kind="missing_destination",
        files_tested=len(TEST_FILES),
        intentional_faults=intentional_faults,
        faults_detected=faults_detected,
        faults_missed=faults_missed,
        detection_rate=100.0 if intentional_faults and passed else 0.0,
        checks_expected=checks_expected,
        checks_passed=checks_passed,
    )


def run_truncated_file_detection(root: Path, source: Path) -> ScenarioResult:
    started = time.perf_counter()
    dest = root / "truncated_dest_primary"
    manifest_path = root / "manifests" / "truncated_file_detection.csv"
    manifest = Manifest(manifest_path)
    copy_leg(source_root=source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest)
    victim = sorted(path for path in dest.rglob("*") if path.is_file() and path.stat().st_size > 8)[0]
    with victim.open("r+b") as handle:
        handle.truncate(max(1, victim.stat().st_size // 2))
    _verify_ok, verify_fail = verify_leg(source_root=source, dest_root=dest, upstream_label="Source", downstream_label="Primary", manifest=manifest, lock=threading.Lock())
    report, report_ok, report_fail = write_scenario_report(manifest_path, "truncated_file_detection", source, dest)

    intentional_faults = 1
    faults_detected = 1 if verify_fail >= intentional_faults and report_fail >= intentional_faults else 0
    faults_missed = intentional_faults - faults_detected
    passed = faults_missed == 0
    checks_expected = report_ok + report_fail
    checks_passed = checks_expected if passed else max(0, checks_expected - faults_missed)
    base_note = (
        "truncated clip detected and flagged: " + str(victim.relative_to(dest))
        if passed
        else "intentionally truncated clip was NOT detected: " + str(victim.relative_to(dest))
    )
    display_note = (
        "MediaRunner intentionally truncated one destination clip. Testing truncated file detection layer. "
        + (
            f"Status: PASS — {checks_passed}/{checks_expected} validation checks passed; "
            "truncated clip detected and flagged."
            if passed
            else f"Status: FAIL — MediaRunner failed to detect {faults_missed}/{intentional_faults} "
            "intentionally truncated clip; investigate checksum/size verification."
        )
    )
    return ScenarioResult(
        "truncated_file_detection",
        "PASS" if passed else "FAIL",
        report_ok,
        report_fail,
        0,
        time.perf_counter() - started,
        str(manifest_path),
        report,
        note=base_note,
        display_name="Truncated File Detection Layer",
        display_ok=checks_passed,
        display_fail=0 if passed else faults_missed,
        display_note=display_note,
        expected_fault_test=True,
        fault_kind="truncated_file",
        files_tested=len(TEST_FILES),
        intentional_faults=intentional_faults,
        faults_detected=faults_detected,
        faults_missed=faults_missed,
        detection_rate=100.0 if intentional_faults and passed else 0.0,
        checks_expected=checks_expected,
        checks_passed=checks_passed,
    )


def run_manifest_report_audit(root: Path, source: Path, prior_results: list[ScenarioResult]) -> ScenarioResult:
    started = time.perf_counter()
    failures = []
    audited = 0
    for result in prior_results:
        manifest_path = Path(result.manifest)
        report_path = Path(result.report)
        if not manifest_path.exists():
            failures.append(f"missing manifest for {result.name}")
            continue
        if not report_path.exists():
            failures.append(f"missing report for {result.name}")
            continue
        # Compare like-for-like: result.ok_rows/fail_rows come from the HTML
        # report's status buckets, so count the manifest with the same
        # bucketing. (Previously this compared against PASS_STATUSES, which
        # also counts "Copied" rows — guaranteeing a false mismatch for any
        # scenario whose manifest holds both copy-pass and verify-pass rows.)
        from mediarunner_core import transfer_status_bucket
        _ok_all, _fail_all, rows = manifest_counts(manifest_path)
        ok = sum(1 for row in rows if transfer_status_bucket(row.get("verification_status") or row.get("status")) == "ok")
        fail = sum(1 for row in rows if transfer_status_bucket(row.get("verification_status") or row.get("status")) == "fail")
        if ok != result.ok_rows or fail != result.fail_rows:
            failures.append(f"count mismatch {result.name}: manifest {ok}/{fail}, result {result.ok_rows}/{result.fail_rows}")
        if getattr(result, "expected_corruption_test", False) and result.status == "PASS":
            if getattr(result, "corruptions_detected", 0) < getattr(result, "intentional_corruptions", 1):
                failures.append(f"corruption detection miss {result.name}: detected {getattr(result, 'corruptions_detected', 0)}")
        audited += 1
    manifest_path = root / "manifests" / "manifest_report_audit.csv"
    manifest = Manifest(manifest_path)
    manifest.write(
        method="AUDIT",
        source_path=str(source),
        destination_path=str(root),
        file="validation_results",
        status="Verified" if not failures else "FAIL",
        note=f"audited={audited}" if not failures else "; ".join(failures[:4]),
    )
    report, report_ok, report_fail = write_scenario_report(manifest_path, "manifest_report_audit", source, root)
    passed = not failures and report_fail == 0
    return ScenarioResult("manifest_report_audit", "PASS" if passed else "FAIL", report_ok, report_fail, 0, time.perf_counter() - started, str(manifest_path), report, note=f"audited={audited}")


def run_stress_repeats(root: Path, *, runs: int, seed: int) -> ScenarioResult:
    started = time.perf_counter()
    failures = []
    bytes_total = 0
    runs = max(1, int(runs))
    for index in range(1, runs + 1):
        subroot = root / "stress_runs" / f"run_{index:03d}"
        subroot.mkdir(parents=True, exist_ok=True)
        source = subroot / "source_media"
        bytes_total += create_test_media(source, seed=seed + index)
        (subroot / "manifests").mkdir(parents=True, exist_ok=True)
        for scenario in (run_single_destination, run_second_pass, run_corruption_detection):
            result = scenario(subroot, source)
            if result.status != "PASS":
                failures.append(f"run {index} {result.name}")
    manifest_path = root / "manifests" / "stress_repeats.csv"
    manifest = Manifest(manifest_path)
    manifest.write(
        method="STRESS",
        source_path=str(root / "stress_runs"),
        destination_path=str(root),
        file="stress_repeats",
        size_bytes=bytes_total,
        size_human=human_size(bytes_total),
        status="Verified" if not failures else "FAIL",
        note=f"runs={runs}" if not failures else "; ".join(failures[:4]),
    )
    report, report_ok, report_fail = write_scenario_report(manifest_path, "stress_repeats", root / "stress_runs", root)
    passed = not failures and report_fail == 0
    return ScenarioResult("stress_repeats", "PASS" if passed else "FAIL", report_ok, report_fail, bytes_total, time.perf_counter() - started, str(manifest_path), report, note=f"runs={runs}")

def validation_logo_data_uri() -> str:
    """Return an embedded report logo data URI, if a canonical logo asset exists."""
    candidates = [
        ROOT / "assets" / "HTML_ReportLogo.png",
        ROOT / "assets" / "HTML_ReportLogo.PNG",
        ROOT / "MediaRunner_LOGO_HTML.png",
        ROOT / "MediaRunner_REPORT_LOGO.png",
        ROOT / "MediaRunner_LOGO.png",
    ]
    for logo in candidates:
        if logo.exists():
            return "data:image/png;base64," + base64.b64encode(logo.read_bytes()).decode("ascii")
    return ""


def write_validation_report(out_path: Path, results: list[ScenarioResult], source_bytes: int, root: Path, profile: str, runs: int) -> None:
    passed = sum(1 for item in results if item.status == "PASS")
    failed = len(results) - passed

    def esc(value: object) -> str:
        return html.escape("" if value is None else str(value))

    logo_uri = validation_logo_data_uri()
    logo_tag = f"<img src='{logo_uri}' class='logo' alt='MediaRunner'>" if logo_uri else ""

    rows = "\n".join(
        "<tr class='{cls}'><td>{name}</td><td>{status}</td><td>{ok}</td><td>{fail}</td><td>{bytes}</td><td>{seconds:.2f}s</td><td>{note}</td><td><code>{manifest}</code></td></tr>".format(
            cls="ok" if item.status == "PASS" else "fail",
            name=esc(item.display_name or item.name),
            status=esc(item.status),
            ok=esc(item.display_ok if item.display_ok != "" else item.ok_rows),
            fail=esc(item.display_fail if item.display_fail != "" else item.fail_rows),
            bytes=esc(human_size(item.bytes_copied)),
            seconds=item.seconds,
            note=esc(item.display_note or item.note),
            manifest=esc(item.manifest),
        )
        for item in results
    )
    html_text = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>MediaRunner Validation Report</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,Helvetica,Arial,sans-serif;margin:32px;color:#172033;background:#f6f8fb}}
.card{{background:white;border:1px solid #d9e1ea;border-radius:14px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px rgba(15,23,42,.06)}}
.header{{display:flex;align-items:center;gap:18px;margin-bottom:6px}}
.logo{{width:180px;max-height:92px;object-fit:contain}}
h1{{margin:0 0 4px;font-size:30px}} .muted{{color:#637083}} .pass{{color:#168a4a;font-weight:800}} .failtxt{{color:#b42318;font-weight:800}}
table{{width:100%;border-collapse:collapse;background:white}} th{{background:#172033;color:white;text-align:left;padding:10px;font-size:12px;text-transform:uppercase;letter-spacing:.06em}} td{{padding:10px;border-bottom:1px solid #e6ebf1;font-size:13px;vertical-align:top}} tr.ok td:nth-child(2){{color:#168a4a;font-weight:800}} tr.fail td:nth-child(2){{color:#b42318;font-weight:800}} code{{font-size:11px;color:#4b5563;word-break:break-all}}
</style></head><body>
<div class='card'><div class='header'>{logo_tag}<div><h1>MediaRunner Validation Report</h1>
<div class='muted'>Generated {esc(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</div></div></div>
<p><strong>Result:</strong> <span class='{ 'pass' if failed == 0 else 'failtxt' }'>{passed} PASS / {failed} FAIL</span></p>
<p><strong>Profile:</strong> {esc(profile)}<br><strong>Stress runs:</strong> {esc(runs)}<br><strong>Generated test payload:</strong> {esc(human_size(source_bytes))}<br><strong>Working root:</strong> <code>{esc(root)}</code></p>
<p class='muted'>This suite validates deterministic local copy/checksum/report invariants and selected edge cases. Use TEST_MATRIX.md for hardware, RED Wireless, low-space, and long-duration production validation.</p></div>
<div class='card'><table><thead><tr><th>Scenario</th><th>Status</th><th>Pass</th><th>Fail</th><th>Bytes copied</th><th>Time</th><th>Note</th><th>Manifest</th></tr></thead><tbody>
{rows}
</tbody></table></div>
</body></html>"""
    out_path.write_text(html_text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run MediaRunner local validation tests.")
    parser.add_argument("--work-dir", help="Optional working directory. Defaults to a temporary folder.")
    parser.add_argument("--keep", action="store_true", help="Keep temporary files when --work-dir is not provided.")
    parser.add_argument("--seed", type=int, default=5700, help="Deterministic test media seed.")
    parser.add_argument("--profile", choices=["quick", "extended", "stress", "stress-field"], default="quick", help="Validation profile to run.")
    parser.add_argument("--runs", type=int, default=10, help="Number of stress repeat cycles when --profile stress is used.")
    args = parser.parse_args()

    if args.work_dir:
        root = Path(args.work_dir).expanduser().resolve()
        if root.exists():
            shutil.rmtree(root)
        root.mkdir(parents=True, exist_ok=True)
        cleanup = False
    else:
        root = Path(tempfile.mkdtemp(prefix="mediarunner_validation_"))
        cleanup = not args.keep

    try:
        source = root / "source_media"
        source_bytes = create_test_media(source, seed=args.seed)
        (root / "manifests").mkdir(parents=True, exist_ok=True)

        scenarios = [
            run_single_destination,
            run_simultaneous,
            run_primary_first,
            run_cascade,
            run_second_pass,
            run_skip_existing,
            run_corruption_detection,
        ]
        if args.profile in ("extended", "stress"):
            scenarios.extend([
                run_empty_folder,
                run_long_nested_paths,
                run_missing_destination_detection,
                run_truncated_file_detection,
            ])
        results: list[ScenarioResult] = []
        for scenario in scenarios:
            result = scenario(root, source)
            results.append(result)
            row_name = result.display_name or result.name
            pass_count = result.display_ok if result.display_ok != "" else result.ok_rows
            fail_count = result.display_fail if result.display_fail != "" else result.fail_rows
            note = result.display_note or result.note
            print(f"{result.status:4}  {row_name:36} pass={pass_count} fail={fail_count} {note}")

        if args.profile in ("extended", "stress"):
            audit = run_manifest_report_audit(root, source, results)
            results.append(audit)
            print(f"{audit.status:4}  {audit.name:36} pass={audit.ok_rows} fail={audit.fail_rows} {audit.note}")

        if args.profile == "stress":
            stress = run_stress_repeats(root, runs=args.runs, seed=args.seed)
            results.append(stress)
            print(f"{stress.status:4}  {stress.name:36} pass={stress.ok_rows} fail={stress.fail_rows} {stress.note}")

        if args.profile == "stress-field":
            # Field-stress harness: fault-injection FTP, SIGKILL, ENOSPC,
            # cancellation fuzzing. Proves the resilience layer automatically.
            import stress_field
            for fs_result in stress_field.run_all(root, ScenarioResult, log=print):
                results.append(fs_result)
                print(f"{fs_result.status:4}  {fs_result.name:36} pass={fs_result.ok_rows} fail={fs_result.fail_rows} {fs_result.note}")

        results_path = root / "validation_results.json"
        report_path = root / "validation_report.html"
        results_path.write_text(
            json.dumps(
                {
                    "schema": "mediarunner_validation_v5",
                    "generated_at": datetime.now().isoformat(timespec="seconds"),
                    "root": str(root),
                    "profile": args.profile,
                    "runs": args.runs,
                    "source_bytes": source_bytes,
                    "python": sys.version.replace("\n", " "),
                    "platform": platform.platform(),
                    "results": [asdict(item) for item in results],
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        write_validation_report(report_path, results, source_bytes, root, args.profile, args.runs)

        print("\nValidation artifacts:")
        print(f"  JSON : {results_path}")
        print(f"  HTML : {report_path}")
        failures = [item for item in results if item.status != "PASS"]
        print("\nResult:", "PASS" if not failures else "FAIL")
        return 0 if not failures else 1
    finally:
        if cleanup:
            # Keep artifacts on failure for easier debugging.
            pass


if __name__ == "__main__":
    raise SystemExit(main())
