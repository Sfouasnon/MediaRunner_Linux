#!/usr/bin/env python3
"""
MediaRunner Transfer — Move landed footage from local storage → network destination.

Preserves RED hierarchy:  CAMERA_LABEL/REEL.RDM/CLIP.RDC/files
Uses xxhash128 for post-copy verification.
Skips files already present at destination with matching hash.

Usage (interactive):  python3 mediarunner_transfer.py
Usage (scripted):
  python3 mediarunner_transfer.py <src_root> <dst_root> <project> <manifest_csv> [clip_filter...]

clip_filter (optional): space-separated labels like "GA GB" or clip names like "G007_A083"
  Omit to transfer everything under src_root.
"""
import sys
import logging
import shutil
import threading
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent))
from mediarunner_core import (
    xxh128,
    Manifest,
    human_size,
    write_html_report,  # noqa: F401
    TransferStatus,
    assess_existing_destination,
    verify_file_pair,
    verify_local_artifact,
    verification_result_to_manifest_kwargs,
    copy_file_to_part,
    copy_file_to_part_with_hash,
    commit_part_file,
    retry_operation,
    FatalTransferError,  # noqa: F401  (re-exported for callers)
    TransferCancelledError,
)

logger = logging.getLogger("mediarunner.transfer")


# ── Discovery ─────────────────────────────────────────────────────────────────
# Folders to always skip regardless of media type
_SKIP_DIRS = {"_checksums", "_manifests", "__pycache__"}

def discover_files(src_root: Path, clip_filter: list[str]) -> list[tuple[Path, str, str, str]]:
    """
    Return list of (file, camera, reel, clip) tuples covering ALL media under src_root.

    Strategy:
      - RED media:   walks CAMERA/REEL.RDM/CLIP.RDC hierarchy, tags accordingly
      - Everything else (blackmagic, hyperdeck, etc.): included as-is, camera/reel/clip
        derived from the first three path components relative to src_root

    clip_filter: if set, only include files whose relative path contains any token.
    """
    tokens = [t.upper() for t in clip_filter] if clip_filter else []
    results = []

    # Walk every file under src_root, skipping system dirs
    for f in sorted(src_root.rglob("*")):
        if not f.is_file():
            continue

        # Skip system/hidden
        if any(part in _SKIP_DIRS or part.startswith(".") for part in f.parts):
            continue

        # Skip in-progress/orphaned partial files (audit fix #9): a stray
        # .part from a cancelled job must never be treated as source media.
        if f.suffix.lower() == ".part":
            continue

        rel   = f.relative_to(src_root)
        parts = rel.parts

        # Apply clip filter
        if tokens and not any(tok in str(rel).upper() for tok in tokens):
            continue

        # Derive metadata from directory components only — the filename must
        # never leak into the Camera column (e.g. a clip sitting at the source
        # root used to report itself as its own camera).
        dirs   = parts[:-1]
        camera = dirs[0] if len(dirs) >= 1 else ""
        reel   = dirs[1] if len(dirs) >= 2 else ""
        clip   = dirs[2] if len(dirs) >= 3 else ""

        results.append((f, camera, reel, clip))

    return results


# ── Per-file transfer + verify ────────────────────────────────────────────────
def transfer_file(src: Path, dst: Path, manifest: Manifest,
                  camera: str, reel: str, clip: str,
                  verify: bool, lock: threading.Lock,
                  progress_callback=None, cancel_check=None) -> bool:
    """
    Copy src → dst if dst doesn't exist or hashes differ.
    Returns True on success.

    Audit fixes applied:
      #1  transient copy/verify failures retry with backoff (retry_count is
          recorded in the manifest);
      #4  cancel_check propagates into the chunked copy so local jobs stop
          promptly;
      #7  the source is hashed during the copy read, so verification only
          re-reads the destination.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    size = src.stat().st_size
    size_h = human_size(size)
    overwrite_note = ""

    if dst.exists():
        existing = assess_existing_destination(src, dst)
        if existing.status == TransferStatus.SKIPPED_EXISTING_VERIFIED:
            with lock:
                manifest.write(**verification_result_to_manifest_kwargs(
                    existing,
                    method="Transfer",
                    camera=camera,
                    reel=reel,
                    clip=clip,
                    file=src.name,
                    size_bytes=size,
                    size_human=size_h,
                ))
            return True
        overwrite_note = "Existing destination failed verification and was replaced"

    if verify:
        def attempt(_attempt_index: int):
            part, src_checksums = copy_file_to_part_with_hash(
                src, dst,
                progress_callback=progress_callback,
                cancel_check=cancel_check,
            )
            attempt_result = verify_file_pair(src, part, source_checksums=src_checksums)
            if attempt_result.status != TransferStatus.VERIFIED:
                # Treat a mismatch as retryable: re-copying recovers transient
                # read/write corruption. Persistent corruption still fails
                # after the retry budget is spent.
                raise IOError(attempt_result.note or "Verification failed")
            return attempt_result

        try:
            result, retries = retry_operation(
                attempt,
                cancel_check=cancel_check,
                description=f"copy+verify {src.name}",
            )
        except (TransferCancelledError, FatalTransferError):
            # Cancellation and fatal conditions (disk full) propagate so the
            # caller can stop the whole job, not just this file.
            raise
        except Exception as exc:
            failed = verify_file_pair(src, _part_or_dst(dst))
            failed.retry_count = max(getattr(failed, "retry_count", 0), 0)
            with lock:
                manifest.write(**verification_result_to_manifest_kwargs(
                    failed,
                    method="Transfer",
                    camera=camera,
                    reel=reel,
                    clip=clip,
                    file=src.name,
                    size_bytes=size,
                    size_human=size_h,
                    status=failed.status if failed.status == TransferStatus.MISMATCH else TransferStatus.FAILED,
                    verification_status=failed.status if failed.status == TransferStatus.MISMATCH else TransferStatus.FAILED,
                    error=str(exc),
                    note=str(exc),
                ))
            logger.error("Transfer failed for %s after retries: %s", src, exc)
            return False

        result.note = overwrite_note or result.note or "Source and destination verified"
        result.retry_count = retries
        result.destination_path = str(dst)  # manifest shows the final path, not the .part
        commit_part_file(_part_or_dst(dst, prefer_part=True), dst)
        with lock:
            manifest.write(**verification_result_to_manifest_kwargs(
                result,
                method="Transfer",
                camera=camera,
                reel=reel,
                clip=clip,
                file=src.name,
                size_bytes=size,
                size_human=size_h,
            ))
        return True

    def attempt_copy(_attempt_index: int):
        part = copy_file_to_part(
            src, dst,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
        )
        local = verify_local_artifact(
            part,
            expected_size=size,
            matched_status=TransferStatus.COPIED,
            note=overwrite_note or "Copied without source verification",
        )
        if local.status == TransferStatus.PARTIAL:
            raise IOError(local.note or "Partial copy")
        return part, local

    try:
        (part, local_result), retries = retry_operation(
            attempt_copy,
            cancel_check=cancel_check,
            description=f"copy {src.name}",
        )
    except (TransferCancelledError, FatalTransferError):
        raise
    except Exception as exc:
        with lock:
            manifest.write(
                method="Transfer",
                source_path=str(src),
                destination_path=str(dst),
                camera=camera, reel=reel, clip=clip, file=src.name,
                size_bytes=size, size_human=size_h,
                status=TransferStatus.FAILED, verification_status=TransferStatus.FAILED,
                error=str(exc), note=str(exc),
            )
        logger.error("Copy failed for %s after retries: %s", src, exc)
        return False

    local_result.retry_count = retries
    commit_part_file(part, dst)
    with lock:
        manifest.write(**verification_result_to_manifest_kwargs(
            local_result,
            method="Transfer",
            source_path=str(src),
            destination_path=str(dst),
            camera=camera,
            reel=reel,
            clip=clip,
            file=src.name,
            size_bytes=size,
            size_human=size_h,
        ))
    return True


def _part_or_dst(dst: Path, prefer_part: bool = True) -> Path:
    part = dst.with_name(dst.name + ".part")
    if prefer_part and part.exists():
        return part
    return part if part.exists() else dst



def copy2_with_progress(src: Path, dst: Path, progress_callback=None, chunk_size: int = 8 * 1024 * 1024,
                        cancel_check=None) -> None:
    """Copy src to dst while reporting actual bytes written.

    This is intentionally simple and app-level: it measures bytes MediaRunner
    writes through Python, not a lower-level bus or disk benchmark. Metadata and
    timestamps are preserved after the byte copy, matching shutil.copy2 behavior.
    """
    part = copy_file_to_part(
        src,
        dst,
        progress_callback=progress_callback,
        chunk_size=chunk_size,
        cancel_check=cancel_check,
    )
    commit_part_file(part, dst)

# ── Main transfer engine ──────────────────────────────────────────────────────
def run_transfer(src_root: Path, dst_root: Path, project: str,
                 manifest: Manifest, clip_filter: list[str],
                 threads: int = 4, verify: bool = True):

    files_meta = discover_files(src_root, clip_filter)
    if not files_meta:
        print("❌  No files found. Check source path or clip filter.")
        return

    # Build transfer list: (src, dst, camera, reel, clip)
    files: list[tuple[Path, Path, str, str, str]] = [
        (f, dst_root / f.relative_to(src_root), cam, reel, clip)
        for f, cam, reel, clip in files_meta
    ]

    total_bytes = sum(f[0].stat().st_size for f in files)
    print(f"\nProject  : {project}")
    print(f"Files    : {len(files)}")
    print(f"Payload  : {human_size(total_bytes)}")
    print(f"Verify   : {'xxhash128' if verify else 'OFF'}")
    print(f"Threads  : {threads}")

    # Capacity check
    stat = shutil.disk_usage(dst_root)
    if total_bytes > stat.free:
        print(f"❌  Insufficient space at destination "
              f"(need {human_size(total_bytes)}, have {human_size(stat.free)})")
        return

    confirm = input("\nBegin transfer? (y/n): ").strip().lower()
    if confirm != "y":
        return

    print(f"\n{'─'*60}")
    lock = threading.Lock()
    ok_count = 0
    fail_count = 0

    with ThreadPoolExecutor(max_workers=threads) as pool:
        futures = {
            pool.submit(transfer_file, src, dst, manifest, cam, reel, clip, verify, lock): src
            for src, dst, cam, reel, clip in files
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            src_path = futures[future]
            try:
                success = future.result()
                if success:
                    ok_count += 1
                    print(f"  ✔  [{done}/{len(files)}]  {src_path.name}")
                else:
                    fail_count += 1
                    print(f"  ✘  [{done}/{len(files)}]  {src_path.name}  ← HASH MISMATCH")
            except Exception as e:
                fail_count += 1
                print(f"  ✘  [{done}/{len(files)}]  {src_path.name}  ← ERROR: {e}")
                with lock:
                    manifest.write(stage="Transfer", file=src_path.name,
                                   status="ERROR", note=str(e))

    print(f"\n{'─'*60}")
    print(f"✔ OK: {ok_count}   ✘ FAIL: {fail_count}   Total: {len(files)}")
    return ok_count, fail_count


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from mediarunner_logging import setup_logging
    setup_logging()
    print("\n=== MediaRunner Transfer — Local → Network ===\n")

    if len(sys.argv) >= 5:
        src_root     = Path(sys.argv[1]).expanduser().resolve()
        dst_root     = Path(sys.argv[2]).expanduser().resolve()
        project      = sys.argv[3]
        manifest_csv = Path(sys.argv[4]).expanduser().resolve()
        clip_filter  = sys.argv[5:] if len(sys.argv) > 5 else []
    else:
        src_root     = Path(input("Source (local storage root): ").strip()).expanduser().resolve()
        dst_root     = Path(input("Destination (network root): ").strip()).expanduser().resolve()
        project      = input("Project name: ").strip()
        manifest_csv = src_root / "_manifests" / "MediaRunner_Session.csv"
        clip_f       = input("Clip filter (blank = transfer all, or e.g. GA G007_A083): ").strip()
        clip_filter  = clip_f.split() if clip_f else []

    threads_in = input("Threads (default 4): ").strip()
    threads    = int(threads_in) if threads_in.isdigit() else 4
    verify_in  = input("Verify with xxhash128? (y/n, default y): ").strip().lower()
    verify     = verify_in != "n"

    dst_root.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(manifest_csv)

    result = run_transfer(src_root, dst_root, project, manifest,
                          clip_filter, threads=threads, verify=verify)

    if result:
        ok, fail = result
        # Generate HTML report
        ts      = datetime.now().strftime("%Y%m%d_%H%M%S")
        report  = manifest_csv.parent / f"MediaRunner_Report_{project}_{ts}.html"
        write_html_report(manifest_csv, project, report)
        print(f"\n📄  Report: {report}")

    print("\n✅  Transfer complete.")
