#!/usr/bin/env python3
"""
MediaRunner FTP — Pull targeted clips OR full reels from the RED camera array.

Usage (interactive):  python3 mediarunner_ftp.py
Usage (scripted):     python3 mediarunner_ftp.py <output_dir> <manifest_csv>

Clip input format (one per line):
  G007_A083        ← reel_clip  (pulls that specific clip)
  ALL:007          ← ALL cameras, reel 007
"""
from __future__ import annotations

import sys
import os
import re
import time
import logging
from pathlib import Path

# Allow running from any directory
sys.path.insert(0, str(Path(__file__).parent))
from mediarunner_core import (
    CAMERAS, is_online, scan_cameras, ftp_connect, ftp_download_dir,
    Manifest, VerificationResult, compute_checksums, human_size, finalize_ftp_manifest,
    TransferStatus, TransferCancelledError, verify_local_artifact,
    verification_result_to_manifest_kwargs,
    retry_operation, ftp_settings_snapshot,
)
from mediarunner_mhl import find_matching_mhl_record, load_clip_mhl, select_preferred_hash

logger = logging.getLogger("mediarunner.ftp")


def pull_clips(clip_list: list[str], output_dir: Path, manifest: Manifest):
    """
    clip_list items:
      "G007_A083"   → camera GA, reel hint G007, clip A083
      "ALL:007"     → all cameras, reel 007
    """
    # Expand ALL: entries
    expanded = []
    for entry in clip_list:
        if entry.upper().startswith("ALL:"):
            reel_num = entry.split(":", 1)[1].strip()
            for label in CAMERAS:
                expanded.append(("ALL", label, reel_num, None))
        else:
            parts = entry.strip().split("_")
            if len(parts) < 2:
                print(f"  ⚠  Skipping unrecognised format: {entry}")
                continue
            reel_hint = parts[0].upper()        # G007
            clip_id   = parts[1].upper()        # A083
            reel_letter = reel_hint[0]           # G
            cam_letter  = clip_id[0]             # A
            label = f"{reel_letter}{cam_letter}" # GA
            expanded.append(("CLIP", label, reel_hint, clip_id))

    # Group by camera to minimise FTP connections
    by_cam: dict[str, list] = {}
    for mode, label, reel_hint, clip_id in expanded:
        by_cam.setdefault(label, []).append((mode, reel_hint, clip_id))

    for label, tasks in sorted(by_cam.items()):
        ip = CAMERAS.get(label)
        if not ip:
            print(f"\n  ⚠  No IP mapping for camera {label}")
            continue

        print(f"\n>>> {label} ({ip})", end="  ", flush=True)
        if not is_online(ip):
            print("❌ OFFLINE")
            for _, reel_hint, clip_id in tasks:
                manifest.write(stage="FTP", camera=label, reel=reel_hint,
                               clip=clip_id or "ALL", status="OFFLINE",
                               note="Camera unreachable")
            continue

        print("ONLINE — connecting...")
        try:
            ftp = ftp_connect(ip)
            ftp.cwd("/media")
            all_reels = [r for r in ftp.nlst() if r.upper().endswith(".RDM")]

            for mode, reel_hint, clip_id in tasks:
                # Find matching reels (sorted so hinted reel comes first)
                matching_reels = sorted(
                    [r for r in all_reels if reel_hint in r.upper()],
                    key=lambda x: reel_hint in x.upper(), reverse=True
                ) or all_reels  # fall back to all reels if no match

                found = False
                for reel in matching_reels:
                    reel_path = f"/media/{reel}"
                    try:
                        ftp.cwd(reel_path)
                        rdc_folders = ftp.nlst()
                    except Exception:
                        continue

                    for rdc in rdc_folders:
                        if not rdc.upper().endswith(".RDC"):
                            continue

                        # CLIP mode: match clip_id in rdc name
                        if mode == "CLIP" and clip_id not in rdc.upper():
                            continue

                        rdc_path = f"{reel_path}/{rdc}"
                        local_clip = output_dir / label / reel / rdc

                        if local_clip.exists():
                            print(f"    ↻  Existing local clip will be re-downloaded: {reel}/{rdc}")

                        print(f"    ↓  {reel}/{rdc}")
                        ftp_download_dir(ftp, rdc_path, local_clip)

                        # Verify downloaded files
                        _verify_clip(local_clip, manifest, label, reel, rdc)
                        found = True

                    if found and mode == "CLIP":
                        break

                if not found:
                    tag = clip_id if clip_id else "ALL"
                    print(f"    ❌ Not found: {reel_hint}/{tag}")
                    manifest.write(stage="FTP", camera=label, reel=reel_hint,
                                   clip=tag, status="NOT FOUND")

            ftp.quit()

        except Exception as e:
            print(f"    ❌ Error: {e}")
            manifest.write(stage="FTP", camera=label, status="ERROR", note=str(e))


def _is_mhl_sidecar(path: Path) -> bool:
    return str(path.suffix or "").lower() == ".mhl"


def _clip_identity_tokens(clip_dir: Path, local_files: list[Path]) -> set[str]:
    tokens = {str(Path(clip_dir).stem or "").upper()}
    for file_path in local_files:
        if _is_mhl_sidecar(file_path):
            continue
        stem = str(file_path.stem or "").upper()
        if stem:
            tokens.add(stem)
        parts = [part for part in stem.split("_") if part]
        if len(parts) >= 3:
            tokens.add("_".join(parts[:3]))
    return {token for token in tokens if token}


def _record_belongs_to_clip(record_name: str, clip_tokens: set[str]) -> bool:
    candidate = str(Path(record_name).stem or record_name or "").upper()
    return any(candidate.startswith(token) for token in clip_tokens if token)


def _local_audit_hash(result: VerificationResult, algorithm: str, file_path: Path) -> str:
    normalized = str(algorithm or "").strip().lower()
    if normalized in result.destination_checksums:
        return result.destination_checksums.get(normalized, "")
    return compute_checksums(file_path, algorithms=(normalized,)).get(normalized, "")


def _write_ftp_manifest_row(
    manifest: Manifest,
    result: VerificationResult,
    *,
    label: str,
    reel: str,
    rdc: str,
    file_path: Path,
    status: str,
    note: str,
    verification_source: str,
    error: str = "",
    src_hash: str = "",
    dst_hash: str = "",
    mhl_path: str = "",
    mhl_algorithm: str = "",
    mhl_expected_hash: str = "",
    mhl_actual_hash: str = "",
    mhl_verified: str = "",
):
    manifest.write(**verification_result_to_manifest_kwargs(
        result,
        method="FTP",
        camera=label,
        reel=reel,
        clip=rdc,
        file=file_path.name,
        source_path="",
        destination_path=str(file_path),
        status=status,
        verification_status=status,
        verification_source=verification_source,
        error=error,
        note=note,
        src_hash=src_hash,
        dst_hash=dst_hash,
        mhl_path=mhl_path,
        mhl_algorithm=mhl_algorithm,
        mhl_expected_hash=mhl_expected_hash,
        mhl_actual_hash=mhl_actual_hash,
        mhl_verified=mhl_verified,
    ))


def _verify_clip(
    clip_dir: Path,
    manifest: Manifest,
    label: str,
    reel: str,
    rdc: str,
    *,
    verify_with_mhl: bool = True,
    require_mhl: bool = False,
    log_callback=None,
) -> int:
    """Checksum every downloaded file and optionally verify it against camera ASC MHL."""

    def emit(text: str):
        if not text:
            return
        if log_callback:
            log_callback(text)
        else:
            print(text)

    clip_dir = Path(clip_dir)
    clip_failures = 0
    # Exclude .part remnants (audit fix #9): a partial from an earlier cancel
    # must not be checksummed or matched against the MHL.
    local_files = [
        path for path in sorted(clip_dir.rglob("*"))
        if path.is_file() and path.suffix.lower() != ".part"
    ]
    clip_tokens = _clip_identity_tokens(clip_dir, local_files)
    mhl_files: list[Path] = []
    mhl_records = []
    mhl_errors: list[str] = []
    matched_record_keys: set[tuple[str, str]] = set()

    if verify_with_mhl:
        mhl_files, mhl_records, mhl_errors = load_clip_mhl(clip_dir)
        if mhl_errors:
            for message in mhl_errors:
                emit(f"      ASC MHL warning: {message}")

    for file_path in local_files:
        result = verify_local_artifact(
            file_path,
            include_md5=True,
            matched_status=TransferStatus.DOWNLOADED,
            note="Downloaded and locally checksummed",
        )
        xxhash_value = result.destination_checksums.get("xxh128", "")
        size = file_path.stat().st_size
        emit(f"      ↓  {file_path.name}  {xxhash_value[:16]}…  ({human_size(size)})")

        if _is_mhl_sidecar(file_path):
            _write_ftp_manifest_row(
                manifest,
                result,
                label=label,
                reel=reel,
                rdc=rdc,
                file_path=file_path,
                status=TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED,
                note="ASC MHL sidecar downloaded. Recorded local audit checksums only.",
                verification_source="local_checksum",
            )
            continue

        if not verify_with_mhl:
            _write_ftp_manifest_row(
                manifest,
                result,
                label=label,
                reel=reel,
                rdc=rdc,
                file_path=file_path,
                status=TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED,
                note="ASC MHL verification disabled. Recorded local audit checksums only.",
                verification_source="local_checksum",
            )
            continue

        if not mhl_files:
            status = TransferStatus.MHL_MISSING if require_mhl else TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED
            note = (
                "ASC MHL required, but no MHL file was found. Enable ASC MHL in camera media settings and regenerate hashes before transfer."
                if require_mhl
                else "No ASC MHL found; camera MHL may be disabled. Recorded local audit checksums only."
            )
            verification_source = "mhl_required_missing" if require_mhl else "local_checksum"
            _write_ftp_manifest_row(
                manifest,
                result,
                label=label,
                reel=reel,
                rdc=rdc,
                file_path=file_path,
                status=status,
                note=note,
                verification_source=verification_source,
                error=note if require_mhl else "",
                mhl_verified="false" if require_mhl else "",
            )
            if require_mhl:
                clip_failures += 1
            continue

        if mhl_errors and not mhl_records:
            status = TransferStatus.FAILED if require_mhl else TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED
            note = (
                f"ASC MHL required, but the MHL file could not be parsed: {mhl_errors[0]}"
                if require_mhl
                else f"ASC MHL file could not be parsed; recorded local audit checksums only. {mhl_errors[0]}"
            )
            _write_ftp_manifest_row(
                manifest,
                result,
                label=label,
                reel=reel,
                rdc=rdc,
                file_path=file_path,
                status=status,
                note=note,
                verification_source="mhl_parse_error" if require_mhl else "local_checksum",
                error=note if require_mhl else "",
                mhl_verified="false" if require_mhl else "",
            )
            if require_mhl:
                clip_failures += 1
            continue

        matched_record = find_matching_mhl_record(file_path, clip_dir, mhl_records)
        if matched_record is None:
            status = TransferStatus.MHL_MISSING if require_mhl else TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED
            note = (
                "ASC MHL required, but no matching MHL entry was found for this file. Enable ASC MHL in camera media settings and regenerate hashes before transfer."
                if require_mhl
                else "No matching ASC MHL entry was found for this file. Recorded local audit checksums only."
            )
            _write_ftp_manifest_row(
                manifest,
                result,
                label=label,
                reel=reel,
                rdc=rdc,
                file_path=file_path,
                status=status,
                note=note,
                verification_source="mhl_entry_missing" if require_mhl else "local_checksum",
                error=note if require_mhl else "",
                mhl_verified="false" if require_mhl else "",
            )
            if require_mhl:
                clip_failures += 1
            continue

        matched_record_keys.add((matched_record.mhl_file, matched_record.relative_path))
        selected_hash, unknown_algorithms = select_preferred_hash(matched_record)
        if selected_hash is None:
            names = ", ".join(unknown_algorithms) or "unknown"
            status = TransferStatus.FAILED if require_mhl else TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED
            note = (
                f"ASC MHL required, but this file uses unsupported MHL hash algorithm(s): {names}."
                if require_mhl
                else f"ASC MHL found, but this file uses unsupported MHL hash algorithm(s): {names}. Recorded local audit checksums only."
            )
            _write_ftp_manifest_row(
                manifest,
                result,
                label=label,
                reel=reel,
                rdc=rdc,
                file_path=file_path,
                status=status,
                note=note,
                verification_source="mhl_unknown_algorithm" if require_mhl else "local_checksum",
                error=note if require_mhl else "",
                mhl_path=matched_record.mhl_file,
                mhl_verified="false" if require_mhl else "",
            )
            if require_mhl:
                clip_failures += 1
            continue

        actual_hash = _local_audit_hash(result, selected_hash.algorithm, file_path)
        if actual_hash == selected_hash.value:
            _write_ftp_manifest_row(
                manifest,
                result,
                label=label,
                reel=reel,
                rdc=rdc,
                file_path=file_path,
                status=TransferStatus.VERIFIED_ASC_MHL,
                note=f"Verified via ASC MHL ({selected_hash.algorithm})",
                verification_source="asc_mhl",
                src_hash=selected_hash.value,
                dst_hash=actual_hash,
                mhl_path=matched_record.mhl_file,
                mhl_algorithm=selected_hash.algorithm,
                mhl_expected_hash=selected_hash.value,
                mhl_actual_hash=actual_hash,
                mhl_verified="true",
            )
        else:
            note = f"ASC MHL mismatch for {file_path.name} using {selected_hash.algorithm}."
            _write_ftp_manifest_row(
                manifest,
                result,
                label=label,
                reel=reel,
                rdc=rdc,
                file_path=file_path,
                status=TransferStatus.MHL_MISMATCH,
                note=note,
                verification_source="mhl_mismatch",
                error=note,
                src_hash=selected_hash.value,
                dst_hash=actual_hash,
                mhl_path=matched_record.mhl_file,
                mhl_algorithm=selected_hash.algorithm,
                mhl_expected_hash=selected_hash.value,
                mhl_actual_hash=actual_hash,
                mhl_verified="false",
            )
            clip_failures += 1

    if verify_with_mhl and mhl_records:
        for record in mhl_records:
            if (record.mhl_file, record.relative_path) in matched_record_keys:
                continue
            if str(record.file_name or "").lower().endswith(".mhl"):
                continue
            if not _record_belongs_to_clip(record.file_name or record.relative_path, clip_tokens):
                continue
            selected_hash, unknown_algorithms = select_preferred_hash(record)
            note = "ASC MHL lists this file, but it is missing from the downloaded clip."
            manifest.write(
                method="FTP",
                camera=label,
                reel=reel,
                clip=rdc,
                file=record.file_name or Path(record.relative_path).name,
                source_path="",
                destination_path=str(clip_dir / record.relative_path),
                source_size=str(record.size_bytes or ""),
                status=TransferStatus.PARTIAL,
                verification_status=TransferStatus.PARTIAL,
                verification_source="mhl_missing_local_file",
                mhl_path=record.mhl_file,
                mhl_algorithm=(selected_hash.algorithm if selected_hash else ",".join(unknown_algorithms[:1])),
                mhl_expected_hash=(selected_hash.value if selected_hash else ""),
                mhl_verified="false",
                error=note,
                note=note,
            )
            emit(f"      ✗  Missing local file referenced by ASC MHL: {record.relative_path}")
            clip_failures += 1

    return clip_failures


def pull_reel_clips(reel: str, clips: str, output_dir: Path, manifest: Manifest,
                    cameras: dict[str, str] | None = None, online_only: bool = True,
                    port: int | None = None, timeout: float | None = None,
                    scan_threads: int = 24, progress_callback=None, cancel_event=None,
                    log_callback=None, file_progress_callback=None,
                    destination_role: str = "", report_callback=None,
                    verify_with_mhl: bool = True, require_mhl: bool = False) -> tuple[int, int]:
    """GUI-friendly threaded FTP camera-array pull by reel and clip range."""
    from mediarunner_core import parse_clip_numbers
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading

    # Snapshot connection settings once per job (audit fix #11): mid-job edits
    # on the Networking page must not change credentials under running workers.
    settings = ftp_settings_snapshot()
    cams = dict(cameras or settings["cameras"])
    ftp_user = settings["user"]
    ftp_pass = settings["password"]

    # Honor "Detect online cameras before download" (online_only): the RED array
    # is partially populated on most shoots (12 / 24 / 36 of 42 connected), so
    # pre-probe the roster concurrently and drop unreachable cameras BEFORE the
    # job counts, dials, or tallies them. Without this the absent cameras are
    # each dialed and recorded as OFFLINE failures, producing a bogus
    # "FTP error 12/42" even though all 12 connected cameras succeeded.
    if online_only and cams:
        roster_size = len(cams)
        statuses = scan_cameras(cams, port=port, timeout=timeout)
        online_cams = {label: ip for label, ip in cams.items() if statuses.get(label)}
        skipped = roster_size - len(online_cams)
        notice = (f"Online check: {len(online_cams)} of {roster_size} cameras reachable"
                  + (f"; skipping {skipped} offline." if skipped else "."))
        if log_callback:
            log_callback(notice)
        else:
            print(notice)
        if not online_cams:
            warn = "No cameras reachable — nothing to transfer. Check power, network, and IP mapping."
            if log_callback:
                log_callback(warn)
            else:
                print(warn)
        cams = online_cams

    reel_digits = ''.join(ch for ch in str(reel or '') if ch.isdigit()).zfill(3)
    wanted = parse_clip_numbers(str(clips or ''))
    requested_scan_threads = int(scan_threads or 1)
    active_camera_count = len(cams) if cams else 0
    worker_cap = 2 if active_camera_count >= 8 else 3
    capped_request = min(max(1, requested_scan_threads), worker_cap)
    max_workers = max(1, min(capped_request, active_camera_count or 1))

    if capped_request != max(1, requested_scan_threads):
        message = f"Auto-throttle FTP workers: requested {requested_scan_threads}, using {max_workers}"
        if log_callback:
            log_callback(message)
        else:
            print(message)

    def cancelled():
        return bool(cancel_event is not None and cancel_event.is_set())

    def emit_log(text: str):
        if not text:
            return
        if log_callback:
            log_callback(text)
        else:
            print(text)

    def rdc_matches_wanted(rdc_name: str) -> bool:
        if not wanted:
            return True
        up = str(rdc_name or "").upper()
        # RED clip folder format: G007_A108_....RDC
        m = re.search(r"^[A-Z]\d{3}_([A-Z])(\d{3})_", up)
        if m:
            return m.group(2) in wanted
        return any(re.search(rf"(?:^|[^0-9]){re.escape(num)}(?:[^0-9]|$)", up) for num in wanted)

    progress_lock = threading.Lock()
    write_lock = threading.Lock()
    done = 0
    total = max(1, len(cams))
    progress_state: dict[str, float] = {}
    progress_state_lock = threading.Lock()

    class LockedManifest:
        path = manifest.path
        def write(self, **kwargs):
            with write_lock:
                manifest.write(**kwargs)

    locked_manifest = LockedManifest()

    def report_progress():
        nonlocal done
        with progress_lock:
            done += 1
            if progress_callback:
                try:
                    progress_callback(done, total)
                except Exception:
                    pass

    def report_file_progress(event: dict):
        if file_progress_callback:
            try:
                file_progress_callback(event)
            except Exception:
                pass

        file_name = str(event.get("file") or Path(str(event.get("remote") or "")).name)
        done_bytes = int(event.get("done") or 0)
        total_bytes = int(event.get("total") or 0)
        if total_bytes > 0:
            pct = (done_bytes / total_bytes) * 100.0
            text = f"FTP progress: {file_name} {pct:.1f}% ({human_size(done_bytes)} / {human_size(total_bytes)})"
        else:
            text = f"FTP progress: {file_name} ({human_size(done_bytes)})"

        now = time.monotonic()
        key = str(event.get("local") or event.get("remote") or file_name)
        with progress_state_lock:
            last_log_at = progress_state.get(key, 0.0)
            if (now - last_log_at) < 1.0 and done_bytes < total_bytes:
                return
            progress_state[key] = now
        emit_log(text)

    def one_camera(label_ip):
        label, ip = label_ip
        if cancelled():
            return 0, 0
        ok_count = 0
        fail_count = 0
        emit_log(f">>> {label} ({ip})")

        # Connection holder so retry hooks can transparently reconnect
        # (audit fix #3): a dropped control connection mid-reel no longer
        # fails every remaining clip on the camera.
        conn: dict[str, object] = {"ftp": None}

        def connect(_attempt_index: int = 0):
            conn["ftp"] = ftp_connect(ip, user=ftp_user, password=ftp_pass, port=port, timeout=timeout)
            return conn["ftp"]

        def close_conn():
            ftp = conn.get("ftp")
            if ftp is None:
                return
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass
            conn["ftp"] = None

        def reconnect(attempt: int, exc: BaseException):
            emit_log(f"    Reconnecting to {label} after error (attempt {attempt}): {exc}")
            close_conn()
            connect()

        def keepalive():
            ftp = conn.get("ftp")
            if ftp is None:
                return
            try:
                ftp.voidcmd("NOOP")
            except Exception:
                # Connection went stale between clips; the next download's
                # retry path will reconnect.
                logger.debug("NOOP keepalive failed for %s", label)

        try:
            retry_operation(connect, cancel_check=cancelled, description=f"connect {label} ({ip})")
        except TransferCancelledError:
            return ok_count, fail_count
        except Exception as exc:
            emit_log(f"OFFLINE: {exc}")
            locked_manifest.write(stage="FTP", camera=label, reel=reel_digits, clip=str(clips),
                                  status="OFFLINE", note=str(exc))
            report_progress()
            return ok_count, fail_count + 1

        # Anchored reel match (audit fix #12): reel 007 must not match 1007.
        reel_pattern = re.compile(rf"(?:^|[^0-9])0*{re.escape(reel_digits.lstrip('0') or '0')}(?:[^0-9]|$)")

        def list_reels(_attempt_index: int = 0):
            ftp = conn["ftp"]
            ftp.cwd('/media')
            return [r for r in ftp.nlst() if str(r).upper().endswith('.RDM') and reel_pattern.search(str(r))]

        try:
            try:
                try:
                    reels, _ = retry_operation(list_reels, cancel_check=cancelled,
                                               on_retry=reconnect, description=f"list reels on {label}")
                except TransferCancelledError:
                    return ok_count, fail_count
                except Exception as exc:
                    emit_log(f"Reel listing failed: {exc}")
                    reels = []

                if not reels:
                    emit_log(f"No reel matching {reel_digits}")
                    locked_manifest.write(stage="FTP", camera=label, reel=reel_digits, clip=str(clips),
                                          status="NOT FOUND", note="No matching reel")
                    return ok_count, fail_count + 1

                found_any = False
                for reel_dir in reels:
                    reel_path = f"/media/{reel_dir}"

                    def list_rdc(_attempt_index: int = 0):
                        return conn["ftp"].nlst(reel_path)

                    try:
                        rdc_folders, _ = retry_operation(list_rdc, cancel_check=cancelled,
                                                         on_retry=reconnect, description=f"list {reel_path} on {label}")
                    except TransferCancelledError:
                        return ok_count, fail_count
                    except Exception as exc:
                        emit_log(f"    Listing failed for {reel_dir}: {exc}")
                        rdc_folders = []

                    for rdc_full in rdc_folders:
                        if cancelled():
                            emit_log("    CANCELLED")
                            return ok_count, fail_count
                        rdc = Path(str(rdc_full)).name
                        if not rdc.upper().endswith('.RDC'):
                            continue
                        if not rdc_matches_wanted(rdc):
                            continue

                        remote = f"{reel_path}/{rdc}"
                        local_clip = output_dir / label / reel_dir / rdc

                        if local_clip.exists():
                            emit_log(f"    Existing local clip will be re-downloaded for verification: {reel_dir}/{rdc}")

                        emit_log(f"    Download {reel_dir}/{rdc}")

                        def download_clip(attempt_index: int):
                            # Interrupted attempts leave .part files that the
                            # resume logic in _ftp_download_file picks up, so a
                            # retry continues rather than restarting (#1, #2).
                            ftp_download_dir(
                                conn["ftp"],
                                remote,
                                local_clip,
                                progress_callback=report_file_progress,
                                cancel_check=cancelled,
                            )

                        try:
                            _, retries = retry_operation(
                                download_clip,
                                cancel_check=cancelled,
                                on_retry=reconnect,
                                description=f"download {reel_dir}/{rdc} from {label}",
                            )
                            if retries:
                                emit_log(f"    Recovered after {retries} retr{'y' if retries == 1 else 'ies'}: {reel_dir}/{rdc}")
                            clip_failures = _verify_clip(
                                local_clip,
                                locked_manifest,
                                label,
                                reel_dir,
                                rdc,
                                verify_with_mhl=verify_with_mhl,
                                require_mhl=require_mhl,
                                log_callback=emit_log,
                            )
                            if clip_failures:
                                fail_count += 1
                            else:
                                ok_count += 1
                            found_any = True
                            keepalive()
                        except TransferCancelledError as exc:
                            emit_log(f"    CANCELLED {reel_dir}/{rdc}")
                            locked_manifest.write(
                                stage="FTP",
                                camera=label,
                                reel=reel_dir,
                                clip=rdc,
                                status=TransferStatus.CANCELLED,
                                verification_status=TransferStatus.CANCELLED,
                                error=str(exc),
                                note="Cancelled during FTP download; partial data may remain as .part files",
                            )
                            fail_count += 1
                            return ok_count, fail_count
                        except Exception as exc:
                            # One clip exhausting its retries no longer aborts
                            # the rest of the camera (audit fix #3).
                            emit_log(f"    FAILED {reel_dir}/{rdc} after retries: {exc}")
                            locked_manifest.write(
                                stage="FTP", camera=label, reel=reel_dir, clip=rdc,
                                status=TransferStatus.FAILED,
                                verification_status=TransferStatus.FAILED,
                                error=str(exc), note=f"Download failed after retries: {exc}",
                            )
                            fail_count += 1
                            try:
                                reconnect(0, exc)
                            except Exception:
                                emit_log(f"    Could not re-establish connection to {label}; stopping camera")
                                return ok_count, fail_count

                if not found_any:
                    locked_manifest.write(stage="FTP", camera=label, reel=reel_digits, clip=str(clips),
                                          status="NOT FOUND")
                    fail_count += 1

            finally:
                close_conn()

        except Exception as exc:
            emit_log(f"ERROR: {exc}")
            locked_manifest.write(stage="FTP", camera=label, reel=reel_digits, clip=str(clips),
                                  status="ERROR", note=str(exc))
            fail_count += 1
        finally:
            report_progress()

        return ok_count, fail_count

    emit_log(f"FTP threading: {max_workers} camera worker(s)")

    ok_total = 0
    fail_total = 0
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(one_camera, item) for item in sorted(cams.items())]
        for fut in as_completed(futures):
            ok, fail = fut.result()
            ok_total += ok
            fail_total += fail

    if manifest.path.exists():
        report_path = finalize_ftp_manifest(
            manifest.path,
            output_dir=output_dir,
            cameras=cams,
            destination_role=destination_role,
            project=f"FTP Reel {reel_digits}",
        )
        if report_path is not None:
            emit_log(f"FTP report: {report_path}")
            if report_callback:
                try:
                    report_callback(report_path)
                except Exception:
                    pass

    return ok_total, fail_total


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    from mediarunner_logging import setup_logging
    setup_logging()
    print("\n=== MediaRunner FTP — RED Array Downloader ===\n")

    if len(sys.argv) >= 3:
        output_dir   = Path(sys.argv[1]).expanduser().resolve()
        manifest_csv = Path(sys.argv[2]).expanduser().resolve()
    else:
        output_dir   = Path(input("Output directory (default: ./media): ").strip() or "media").expanduser().resolve()
        manifest_csv = output_dir / "_manifests" / "MediaRunner_Session.csv"

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = Manifest(manifest_csv)
    print(f"📁 Output : {output_dir}")
    print(f"📋 Manifest: {manifest_csv}\n")

    print("Clip names — one per line (e.g. G007_A083 or ALL:007).")
    print("Blank line or Ctrl-D to begin.\n")

    clips = []
    while True:
        try:
            line = input().strip()
            if not line:
                break
            clips.append(line)
        except EOFError:
            break

    if not clips:
        print("No clips provided. Exiting.")
        sys.exit(0)

    pull_clips(clips, output_dir, manifest)
    print(f"\n✅  FTP pull complete. Manifest: {manifest_csv}")
