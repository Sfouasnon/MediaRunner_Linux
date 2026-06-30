#!/usr/bin/env python3
from __future__ import annotations

import csv
import hashlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediarunner_core import Manifest, TransferStatus, compute_checksums, write_html_report  # noqa: E402
from mediarunner_ftp import _verify_clip  # noqa: E402
from mediarunner_mhl import parse_mhl_file  # noqa: E402


def _mhl_xml(entries: list[dict[str, str]], *, namespace: bool = True) -> str:
    if namespace:
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<ascmhl:hashlist xmlns:ascmhl="urn:ASC:MHL:v2.0">']
        prefix = "ascmhl:"
        close = "</ascmhl:hashlist>"
    else:
        lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<hashlist>"]
        prefix = ""
        close = "</hashlist>"
    for entry in entries:
        path_value = entry["path"]
        algorithm = entry["algorithm"]
        digest = entry["hash"]
        action = entry.get("action", "original")
        size_attr = f' size="{entry.get("size", "")}"' if entry.get("size") else ""
        lines.extend([
            f"  <{prefix}hash>",
            f"    <{prefix}path{size_attr}>{path_value}</{prefix}path>",
            f'    <{prefix}{algorithm} action="{action}">{digest}</{prefix}{algorithm}>',
            f"  </{prefix}hash>",
        ])
    lines.append(close)
    return "\n".join(lines) + "\n"


def _read_rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


class MHLVerificationTests(unittest.TestCase):
    def test_parse_valid_asc_mhl_with_namespace(self):
        with TemporaryDirectory() as td:
            mhl_path = Path(td) / "clip.mhl"
            mhl_path.write_text(_mhl_xml([{
                "path": "A001_C001_000001.R3D",
                "algorithm": "sha256",
                "hash": "ab" * 32,
                "size": "16",
            }], namespace=True), encoding="utf-8")
            records = parse_mhl_file(mhl_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].relative_path, "A001_C001_000001.R3D")
            self.assertEqual(records[0].hashes[0].algorithm, "sha256")

    def test_parse_valid_asc_mhl_without_namespace(self):
        with TemporaryDirectory() as td:
            mhl_path = Path(td) / "clip.mhl"
            mhl_path.write_text(_mhl_xml([{
                "path": "A001_C001_000001.R3D",
                "algorithm": "md5",
                "hash": "cd" * 16,
            }], namespace=False), encoding="utf-8")
            records = parse_mhl_file(mhl_path)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].file_name, "A001_C001_000001.R3D")
            self.assertEqual(records[0].hashes[0].algorithm, "md5")

    def test_verify_matching_downloaded_file_marks_verified_via_asc_mhl(self):
        with TemporaryDirectory() as td:
            clip_dir = Path(td) / "A001_A001_000001.RDC"
            clip_dir.mkdir()
            media = clip_dir / "A001_A001_000001.R3D"
            media.write_bytes(b"media-runner-mhl")
            sha256 = compute_checksums(media, algorithms=("sha256",))["sha256"]
            (clip_dir / "clip.mhl").write_text(_mhl_xml([{
                "path": media.name,
                "algorithm": "sha256",
                "hash": sha256,
                "size": str(media.stat().st_size),
            }]), encoding="utf-8")
            manifest_path = Path(td) / "manifest.csv"
            _verify_clip(clip_dir, Manifest(manifest_path), "AA", "A001.RDM", clip_dir.name, verify_with_mhl=True, require_mhl=False)
            row = next(item for item in _read_rows(manifest_path) if item["file"] == media.name)
            self.assertEqual(row["status"], TransferStatus.VERIFIED_ASC_MHL)
            self.assertEqual(row["verification_source"], "asc_mhl")
            self.assertEqual(row["mhl_verified"], "true")

    def test_detect_hash_mismatch(self):
        with TemporaryDirectory() as td:
            clip_dir = Path(td) / "A001_A001_000001.RDC"
            clip_dir.mkdir()
            media = clip_dir / "A001_A001_000001.R3D"
            media.write_bytes(b"media-runner-mhl-mismatch")
            wrong_hash = hashlib.sha256(b"wrong").hexdigest()
            (clip_dir / "clip.mhl").write_text(_mhl_xml([{
                "path": media.name,
                "algorithm": "sha256",
                "hash": wrong_hash,
            }]), encoding="utf-8")
            manifest_path = Path(td) / "manifest.csv"
            failures = _verify_clip(clip_dir, Manifest(manifest_path), "AA", "A001.RDM", clip_dir.name, verify_with_mhl=True, require_mhl=False)
            row = next(item for item in _read_rows(manifest_path) if item["file"] == media.name)
            self.assertEqual(failures, 1)
            self.assertEqual(row["status"], TransferStatus.MHL_MISMATCH)
            self.assertEqual(row["mhl_verified"], "false")

    def test_detect_missing_downloaded_file_from_mhl(self):
        with TemporaryDirectory() as td:
            clip_dir = Path(td) / "A001_A001_000001.RDC"
            clip_dir.mkdir()
            (clip_dir / "clip.mhl").write_text(_mhl_xml([{
                "path": "A001_A001_000001.R3D",
                "algorithm": "sha256",
                "hash": "ef" * 32,
            }]), encoding="utf-8")
            manifest_path = Path(td) / "manifest.csv"
            failures = _verify_clip(clip_dir, Manifest(manifest_path), "AA", "A001.RDM", clip_dir.name, verify_with_mhl=True, require_mhl=False)
            row = next(item for item in _read_rows(manifest_path) if item["file"] == "A001_A001_000001.R3D")
            self.assertEqual(failures, 1)
            self.assertEqual(row["status"], TransferStatus.PARTIAL)
            self.assertEqual(row["verification_source"], "mhl_missing_local_file")

    def test_detect_missing_mhl_entry(self):
        with TemporaryDirectory() as td:
            clip_dir = Path(td) / "A001_A001_000001.RDC"
            clip_dir.mkdir()
            media = clip_dir / "A001_A001_000001.R3D"
            media.write_bytes(b"clip-no-entry")
            (clip_dir / "clip.mhl").write_text(_mhl_xml([{
                "path": "OTHER.R3D",
                "algorithm": "sha256",
                "hash": "01" * 32,
            }]), encoding="utf-8")
            manifest_path = Path(td) / "manifest.csv"
            _verify_clip(clip_dir, Manifest(manifest_path), "AA", "A001.RDM", clip_dir.name, verify_with_mhl=True, require_mhl=False)
            row = next(item for item in _read_rows(manifest_path) if item["file"] == media.name)
            self.assertEqual(row["status"], TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED)
            self.assertEqual(row["verification_source"], "local_checksum")

    def test_verify_disabled_keeps_downloaded_local_only(self):
        with TemporaryDirectory() as td:
            clip_dir = Path(td) / "A001_A001_000001.RDC"
            clip_dir.mkdir()
            media = clip_dir / "A001_A001_000001.R3D"
            media.write_bytes(b"clip-disabled")
            sha256 = compute_checksums(media, algorithms=("sha256",))["sha256"]
            (clip_dir / "clip.mhl").write_text(_mhl_xml([{
                "path": media.name,
                "algorithm": "sha256",
                "hash": sha256,
            }]), encoding="utf-8")
            manifest_path = Path(td) / "manifest.csv"
            _verify_clip(clip_dir, Manifest(manifest_path), "AA", "A001.RDM", clip_dir.name, verify_with_mhl=False, require_mhl=False)
            row = next(item for item in _read_rows(manifest_path) if item["file"] == media.name)
            self.assertEqual(row["status"], TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED)
            self.assertEqual(row["verification_source"], "local_checksum")

    def test_missing_mhl_is_warning_when_not_required(self):
        with TemporaryDirectory() as td:
            clip_dir = Path(td) / "A001_A001_000001.RDC"
            clip_dir.mkdir()
            media = clip_dir / "A001_A001_000001.R3D"
            media.write_bytes(b"clip-no-mhl")
            manifest_path = Path(td) / "manifest.csv"
            failures = _verify_clip(clip_dir, Manifest(manifest_path), "AA", "A001.RDM", clip_dir.name, verify_with_mhl=True, require_mhl=False)
            row = next(item for item in _read_rows(manifest_path) if item["file"] == media.name)
            self.assertEqual(failures, 0)
            self.assertEqual(row["status"], TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED)
            self.assertIn("No ASC MHL found", row["note"])

    def test_missing_mhl_is_failure_when_required(self):
        with TemporaryDirectory() as td:
            clip_dir = Path(td) / "A001_A001_000001.RDC"
            clip_dir.mkdir()
            media = clip_dir / "A001_A001_000001.R3D"
            media.write_bytes(b"clip-mhl-required")
            manifest_path = Path(td) / "manifest.csv"
            failures = _verify_clip(clip_dir, Manifest(manifest_path), "AA", "A001.RDM", clip_dir.name, verify_with_mhl=True, require_mhl=True)
            row = next(item for item in _read_rows(manifest_path) if item["file"] == media.name)
            self.assertEqual(failures, 1)
            self.assertEqual(row["status"], TransferStatus.MHL_MISSING)
            self.assertEqual(row["verification_source"], "mhl_required_missing")

    def test_unknown_mhl_hash_algorithm_does_not_crash(self):
        with TemporaryDirectory() as td:
            clip_dir = Path(td) / "A001_A001_000001.RDC"
            clip_dir.mkdir()
            media = clip_dir / "A001_A001_000001.R3D"
            media.write_bytes(b"clip-unknown-alg")
            (clip_dir / "clip.mhl").write_text(_mhl_xml([{
                "path": media.name,
                "algorithm": "c4",
                "hash": "feedbeef",
            }]), encoding="utf-8")
            manifest_path = Path(td) / "manifest.csv"
            failures = _verify_clip(clip_dir, Manifest(manifest_path), "AA", "A001.RDM", clip_dir.name, verify_with_mhl=True, require_mhl=False)
            row = next(item for item in _read_rows(manifest_path) if item["file"] == media.name)
            self.assertEqual(failures, 0)
            self.assertEqual(row["status"], TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED)
            self.assertIn("unsupported MHL hash algorithm", row["note"])

    def test_report_counts_verified_via_asc_mhl_as_success(self):
        with TemporaryDirectory() as td:
            manifest_path = Path(td) / "manifest.csv"
            report_path = Path(td) / "report.html"
            manifest = Manifest(manifest_path)
            manifest.write(status=TransferStatus.VERIFIED_ASC_MHL, verification_status=TransferStatus.VERIFIED_ASC_MHL, file="verified.r3d")
            manifest.write(status=TransferStatus.MHL_MISMATCH, verification_status=TransferStatus.MHL_MISMATCH, file="bad.r3d")
            ok, fail = write_html_report(manifest_path, "MHL", report_path)
            self.assertEqual(ok, 1)
            self.assertEqual(fail, 1)

    def test_report_does_not_count_local_only_or_missing_mhl_as_verified(self):
        with TemporaryDirectory() as td:
            manifest_path = Path(td) / "manifest.csv"
            report_path = Path(td) / "report.html"
            manifest = Manifest(manifest_path)
            manifest.write(status=TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED, verification_status=TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED, file="local-only.r3d")
            manifest.write(status=TransferStatus.MHL_MISSING, verification_status=TransferStatus.MHL_MISSING, file="missing.r3d")
            ok, fail = write_html_report(manifest_path, "MHL", report_path)
            html = report_path.read_text(encoding="utf-8")
            self.assertEqual(ok, 0)
            self.assertEqual(fail, 1)
            self.assertIn("Unverified: 1", html)


if __name__ == "__main__":
    unittest.main()
