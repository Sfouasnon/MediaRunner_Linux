#!/usr/bin/env python3
"""
MediaRunner — Unified Launcher
Apple Silicon · RED Array · xxhash128 verified transfers

Modes:
  ftp        Pull clips from camera array → local storage
  transfer   Local storage → network destination (verified)
  meta       Scrape R3D LTC timecode → master CSV
  all        ftp → transfer → meta in sequence

Usage:
  python3 mediarunner.py [mode]

  Or just run with no args for interactive menu.
"""
import sys
import os
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))


BANNER = """
╔══════════════════════════════════════════════╗
║         MediaRunner  v0.3.0-beta            ║
║   RED Array · xxhash128 · Apple Silicon      ║
╚══════════════════════════════════════════════╝
"""

MENU = """
  [1]  ftp       — Pull clips from camera array
  [2]  transfer  — Local → Network (verified)
  [3]  meta      — Scrape R3D LTC timecode
  [4]  all       — FTP → Transfer → Meta
  [q]  quit
"""


def run_ftp():
    from mediarunner_ftp import pull_clips
    from mediarunner_core import Manifest

    output_dir   = Path(input("Output (local storage root): ").strip()).expanduser().resolve()
    manifest_csv = output_dir / "_manifests" / "MediaRunner_Session.csv"
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(manifest_csv)

    print("\nClip names — one per line (e.g. G007_A083 or ALL:007).")
    print("Blank line to begin.\n")
    clips = []
    while True:
        try:
            line = input().strip()
            if not line:
                break
            clips.append(line)
        except EOFError:
            break

    if clips:
        pull_clips(clips, output_dir, manifest)
        print(f"\n📋  Manifest: {manifest_csv}")
    else:
        print("No clips provided.")

    return output_dir, manifest_csv


def run_transfer(src_root=None, manifest_csv=None):
    from mediarunner_transfer import run_transfer as _transfer
    from mediarunner_core import Manifest, write_html_report

    if src_root is None:
        src_root = Path(input("Source (local storage root): ").strip()).expanduser().resolve()
    if manifest_csv is None:
        manifest_csv = src_root / "_manifests" / "MediaRunner_Session.csv"

    dst_root   = Path(input("Destination (network root): ").strip()).expanduser().resolve()
    project    = input("Project name: ").strip()
    clip_f     = input("Clip filter (blank = all, or e.g. GA G007_A083): ").strip()
    clip_filter = clip_f.split() if clip_f else []
    thr_in     = input("Threads (default 4): ").strip()
    threads    = int(thr_in) if thr_in.isdigit() else 4
    verify_in  = input("Verify xxhash128? (y/n, default y): ").strip().lower()
    verify     = verify_in != "n"

    dst_root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(manifest_csv)

    result = _transfer(src_root, dst_root, project, manifest,
                       clip_filter, threads=threads, verify=verify)
    if result:
        ts     = datetime.now().strftime("%Y%m%d_%H%M%S")
        report = manifest_csv.parent / f"MediaRunner_Report_{project}_{ts}.html"
        write_html_report(manifest_csv, project, report)
        print(f"\n📄  Report: {report}")

    return src_root, manifest_csv


def run_meta(r3d_root=None, manifest_csv=None):
    from mediarunner_meta import run_meta as _meta
    from mediarunner_core import Manifest

    if r3d_root is None:
        r3d_root = Path(input("R3D folder: ").strip()).expanduser().resolve()
    if manifest_csv is None:
        manifest_csv = r3d_root / "_manifests" / "MediaRunner_Session.csv"

    pf_dir = Path(input("Per-frame CSV output folder: ").strip()).expanduser().resolve()
    ts_tag = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    master = pf_dir / f"master_ltc_{ts_tag}.csv"
    manifest = Manifest(manifest_csv)

    _meta(r3d_root, pf_dir, manifest, master)


def run_all():
    print("\n── Step 1 of 3: FTP Pull ──")
    local_root, manifest_csv = run_ftp()

    print("\n── Step 2 of 3: Transfer ──")
    run_transfer(src_root=local_root, manifest_csv=manifest_csv)

    print("\n── Step 3 of 3: Meta Scrape ──")
    run_meta(r3d_root=local_root, manifest_csv=manifest_csv)

    print("\n✅  All stages complete.")


def main():
    from mediarunner_logging import setup_logging
    setup_logging()
    print(BANNER)

    mode = sys.argv[1].lower() if len(sys.argv) > 1 else None

    if mode is None:
        print(MENU)
        choice = input("Select mode: ").strip().lower()
        mode = {"1": "ftp", "2": "transfer", "3": "meta", "4": "all"}.get(choice, choice)

    dispatch = {
        "ftp":      run_ftp,
        "transfer": run_transfer,
        "meta":     run_meta,
        "all":      run_all,
    }

    fn = dispatch.get(mode)
    if fn is None:
        print(f"Unknown mode: {mode}")
        print("Valid modes: ftp | transfer | meta | all")
        sys.exit(1)

    fn()


if __name__ == "__main__":
    main()
