#!/usr/bin/env python3
"""MediaRunner local install checker."""
from __future__ import annotations

import importlib.util
import py_compile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CANONICAL_FILES = [
    "mediarunner_gui.py",
    "mediarunner_core.py",
    "mediarunner_ftp.py",
    "mediarunner_transfer.py",
    "mediarunner_meta.py",
    "mediarunner_red_wireless.py",
    "mediarunner_reports.py",
    "mediarunner.py",
]
REQUIRED_FILES = CANONICAL_FILES + [
    "assets/UIandMetadata_Logo.png",
    "assets/HTML_ReportLogo.png",
    "MediaRunner_LOGO.png",
    "MediaRunner_LOGO_HTML.png",
    "MediaRunner_REPORT_LOGO.png",
    "validation/run_validation_suite.py",
    "validation/TEST_MATRIX.md",
    "validation/RELEASE_CHECKLIST.md",
]
# xxhash is required: transfers verify with xxh128 and only fall back to
# sha256 (with a loud error) when it is missing. Install: pip3 install xxhash
REQUIRED_MODULES = ["PySide6", "xxhash"]
OPTIONAL_MODULES = []
# The build marker is read from MEDIARUNNER_BUILD_ID in mediarunner_gui.py so
# this check can never go stale on a version bump.
BUILD_MARKER_PREFIX = "MediaRunner Version"


def check_file(name: str) -> bool:
    ok = (ROOT / name).exists()
    print(("✓" if ok else "✗"), name)
    return ok


def check_module(name: str, required: bool = True) -> bool:
    ok = importlib.util.find_spec(name) is not None
    print(("✓" if ok else "✗"), f"Python module {name} ({'required' if required else 'optional'})")
    return ok or not required


def main() -> int:
    print("MediaRunner install check\n")
    ok = True

    for name in REQUIRED_FILES:
        ok = check_file(name) and ok

    gui = ROOT / "mediarunner_gui.py"
    if gui.exists():
        text = gui.read_text(encoding="utf-8", errors="replace")
        marker = ""
        for line in text.splitlines():
            if line.startswith("MEDIARUNNER_BUILD_ID"):
                marker = line.split("=", 1)[-1].strip().strip('"\'')
                break
        has_marker = marker.startswith(BUILD_MARKER_PREFIX)
        print(("✓" if has_marker else "✗"), f"GUI build marker: {marker or 'MEDIARUNNER_BUILD_ID missing'}")
        ok = has_marker and ok

    for name in REQUIRED_MODULES:
        ok = check_module(name, True) and ok

    for name in OPTIONAL_MODULES:
        check_module(name, False)

    try:
        from mediarunner_meta import probe_metadata_tools
        print("\nMetadata tools:")
        probes = probe_metadata_tools({})
        for key in ("redline", "ffmpeg", "ffprobe", "exiftool"):
            probe = probes[key]
            required_note = "optional"
            if key == "redline":
                required_note = "required only for RED/R3D metadata"
            elif key == "ffprobe":
                required_note = "required for generic video metadata"
            print(("✓" if probe.ok else "!"), f"{probe.label} ({required_note}): {probe.path or probe.message}")
    except Exception as exc:
        print("! Metadata tool probe failed", exc)

    print("\nSyntax compile:")
    for name in CANONICAL_FILES + ["validation/run_validation_suite.py", "validation/generate_test_media.py", "validation/inject_checksum_failure.py"]:
        try:
            py_compile.compile(str(ROOT / name), doraise=True)
            print("✓", name)
        except Exception as exc:
            print("✗", name, exc)
            ok = False

    print("\nResult:", "PASS" if ok else "CHECK FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
