#!/usr/bin/env python3
from __future__ import annotations

import csv
import threading
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediarunner_core import (  # noqa: E402
    Manifest,
    TransferCancelledError,
    TransferStatus,
    _ftp_download_file,
    compute_checksums,
    transfer_status_bucket,
    verification_result_to_manifest_kwargs,
    verify_file_pair,
    verify_local_artifact,
    write_html_report,
)
from mediarunner_gui import format_mediarunner_clock, mediarunner_clock_tokens  # noqa: E402
from mediarunner_ftp import ftp_download_worker_count  # noqa: E402
from mediarunner_meta import metadata_worker_count  # noqa: E402
from mediarunner_transfer import copy2_with_progress, transfer_file  # noqa: E402


class FakeFTP:
    def __init__(self, chunks: list[bytes]):
        self.chunks = list(chunks)
        self.cancel_check = None
        self.cancel_after_chunks = 0

    def size(self, remote: str) -> int:
        return sum(len(chunk) for chunk in self.chunks)

    def retrbinary(self, command: str, callback, blocksize: int | None = None, rest: int | None = None):
        offset = max(0, int(rest or 0))
        sent = 0
        for chunk in self.chunks:
            if self.cancel_check and self.cancel_check():
                raise TransferCancelledError("Cancelled by fake FTP stream")
            next_sent = sent + len(chunk)
            if next_sent <= offset:
                sent = next_sent
                continue
            start = max(0, offset - sent)
            callback(chunk[start:])
            sent = next_sent
            if self.cancel_after_chunks and sent >= self.cancel_after_chunks:
                raise TransferCancelledError("Cancelled by fake FTP stream")


class ProductionHardeningTests(unittest.TestCase):
    def test_checksum_generation_supports_xxh128_sha256_and_md5(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "clip.bin"
            path.write_bytes(b"MediaRunner checksum test payload")
            checksums = compute_checksums(path, include_md5=True)
            self.assertTrue(checksums["xxh128"])
            self.assertEqual(len(checksums["sha256"]), 64)
            self.assertEqual(len(checksums["md5"]), 32)

    def test_checksum_mismatch_is_detected(self):
        with TemporaryDirectory() as td:
            src = Path(td) / "src.bin"
            dst = Path(td) / "dst.bin"
            src.write_bytes(b"A" * 128)
            dst.write_bytes(b"B" * 128)
            result = verify_file_pair(src, dst)
            self.assertEqual(result.status, TransferStatus.MISMATCH)
            self.assertEqual(transfer_status_bucket(result.status), "fail")

    def test_existing_corrupted_destination_is_not_skipped_as_verified(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src.bin"
            dst = root / "dst.bin"
            manifest_path = root / "manifest.csv"
            src.write_bytes(b"correct-bytes" * 2048)
            dst.write_bytes(b"wrong-bytes__" * 2048)
            manifest = Manifest(manifest_path)
            ok = transfer_file(src, dst, manifest, "AA", "001.RDM", "A001.RDC", True, threading.Lock())
            self.assertTrue(ok)
            self.assertEqual(src.read_bytes(), dst.read_bytes())
            with manifest_path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[-1]["status"], TransferStatus.VERIFIED)
            self.assertNotEqual(rows[-1]["status"], TransferStatus.SKIPPED_EXISTING_VERIFIED)
            self.assertIn("replaced", rows[-1]["note"].lower())

    def test_copy_uses_part_file_until_successful_rename(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            src = root / "src.bin"
            dst = root / "dst.bin"
            src.write_bytes(b"X" * (1024 * 1024))
            part = dst.with_name(dst.name + ".part")
            seen = {"during_copy": False}

            def progress(_n: int) -> None:
                seen["during_copy"] = True
                self.assertTrue(part.exists())
                self.assertFalse(dst.exists())

            copy2_with_progress(src, dst, progress_callback=progress, chunk_size=64 * 1024)
            self.assertTrue(seen["during_copy"])
            self.assertTrue(dst.exists())
            self.assertFalse(part.exists())
            self.assertEqual(src.read_bytes(), dst.read_bytes())

    def test_cancelled_partial_ftp_download_is_not_verified(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            dst = root / "ftp.bin"
            part = dst.with_name(dst.name + ".part")
            chunks = [b"A" * 32, b"B" * 32, b"C" * 32]
            ftp = FakeFTP(chunks)
            ftp.cancel_after_chunks = 32
            cancelled = {"flag": False}

            def progress(event: dict) -> None:
                if int(event.get("done") or 0) >= 32:
                    cancelled["flag"] = True

            ftp.cancel_check = lambda: cancelled["flag"]
            with self.assertRaises(TransferCancelledError):
                _ftp_download_file(
                    ftp,
                    "/remote/file.bin",
                    dst,
                    progress_callback=progress,
                    cancel_check=lambda: cancelled["flag"],
                )
            self.assertFalse(dst.exists())
            self.assertTrue(part.exists())
            partial = verify_local_artifact(
                part,
                expected_size=sum(len(chunk) for chunk in chunks),
                matched_status=TransferStatus.DOWNLOADED,
            )
            self.assertEqual(partial.status, TransferStatus.PARTIAL)
            self.assertNotEqual(partial.status, TransferStatus.VERIFIED)

    def test_manifest_rows_include_verification_fields(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            dst = root / "verified.bin"
            dst.write_bytes(b"verified-data")
            manifest_path = root / "manifest.csv"
            manifest = Manifest(manifest_path)
            result = verify_local_artifact(dst, expected_size=dst.stat().st_size, matched_status=TransferStatus.DOWNLOADED)
            manifest.write(**verification_result_to_manifest_kwargs(
                result,
                method="FTP",
                file=dst.name,
                destination_path=str(dst),
            ))
            with manifest_path.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            row = rows[-1]
            self.assertTrue(row["checksum_algorithm"])
            self.assertTrue(row["verification_time"])
            self.assertTrue(row["verification_status"])
            self.assertTrue(row["xxhash"])
            self.assertTrue(row["sha256"])

    def test_ftp_download_workers_do_not_hard_cap_large_arrays(self):
        workers, requested = ftp_download_worker_count(36, scan_threads=24, download_workers=24)
        self.assertEqual(requested, 24)
        self.assertEqual(workers, 24)

        workers, requested = ftp_download_worker_count(36, scan_threads=24, download_workers=42)
        self.assertEqual(requested, 42)
        self.assertEqual(workers, 36)

    def test_metadata_workers_are_configurable_and_bounded(self):
        self.assertEqual(metadata_worker_count(100, 8), 8)
        self.assertEqual(metadata_worker_count(3, 8), 3)
        self.assertEqual(metadata_worker_count(100, 99), 24)
        self.assertEqual(metadata_worker_count(100, 0), 1)

    def test_report_summary_counts_only_verified_success(self):
        with TemporaryDirectory() as td:
            root = Path(td)
            manifest_path = root / "manifest.csv"
            report_path = root / "report.html"
            manifest = Manifest(manifest_path)
            rows = [
                {"status": TransferStatus.VERIFIED, "verification_status": TransferStatus.VERIFIED, "file": "verified.mov"},
                {"status": TransferStatus.SKIPPED_EXISTING_VERIFIED, "verification_status": TransferStatus.SKIPPED_EXISTING_VERIFIED, "file": "skip.r3d"},
                {"status": TransferStatus.COPIED, "verification_status": TransferStatus.COPIED, "file": "copied.mov"},
                {"status": TransferStatus.DOWNLOADED, "verification_status": TransferStatus.DOWNLOADED, "file": "downloaded.r3d"},
                {"status": TransferStatus.FAILED, "verification_status": TransferStatus.FAILED, "file": "failed.mov"},
            ]
            for row in rows:
                manifest.write(**row)
            ok, fail = write_html_report(manifest_path, "Hardening", report_path)
            html = report_path.read_text(encoding="utf-8")
            self.assertEqual(ok, 2)
            self.assertEqual(fail, 1)
            self.assertIn("Unverified: 2", html)

    def test_clock_tokens_preserve_mm_dd_yyyy_dash_layout(self):
        dt = datetime(2026, 6, 8, 14, 5, 9)
        text = format_mediarunner_clock(dt)
        self.assertEqual(text, "06-08-2026 02:05:09")
        self.assertEqual(mediarunner_clock_tokens(text)[:10], list("06-08-2026"))


if __name__ == "__main__":
    unittest.main()
