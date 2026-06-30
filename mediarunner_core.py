#!/usr/bin/env python3
from __future__ import annotations

"""
MediaRunner Core - Shared utilities for camera map, clip parsing, hashing, manifests.
"""
import os
import csv
import json
import time
import errno
import socket
import logging
import hashlib
import subprocess
import shutil
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from ftplib import FTP_TLS, error_perm, error_temp
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

logger = logging.getLogger("mediarunner.core")

# ── Retry policy (audit fix #1) ───────────────────────────────────────────────
# Transient I/O and network errors are retried with exponential backoff before a
# file is declared failed. ENOSPC and cancellation are never retried.
RETRY_ATTEMPTS = int(os.environ.get("MEDIARUNNER_RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF_BASE = float(os.environ.get("MEDIARUNNER_RETRY_BACKOFF", "1.5"))


class FatalTransferError(RuntimeError):
    """Raised for conditions that must not be retried (e.g. disk full)."""


def is_disk_full_error(exc: BaseException) -> bool:
    return isinstance(exc, OSError) and getattr(exc, "errno", None) == errno.ENOSPC


def retry_operation(
    operation: Callable[[int], object],
    *,
    attempts: int | None = None,
    backoff_base: float | None = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    on_retry: Optional[Callable[[int, BaseException], None]] = None,
    description: str = "operation",
):
    """Run operation(attempt_index) with bounded retries and backoff.

    Returns (result, retries_used). Re-raises immediately on cancellation or
    fatal errors (disk full). on_retry(attempt, exc) is called before each
    retry sleep so callers can reconnect or log.
    """
    attempts = max(1, int(attempts if attempts is not None else RETRY_ATTEMPTS))
    backoff_base = float(backoff_base if backoff_base is not None else RETRY_BACKOFF_BASE)
    last_exc: BaseException | None = None
    for attempt in range(attempts):
        if cancel_check and cancel_check():
            raise TransferCancelledError(f"Cancelled before {description}")
        try:
            return operation(attempt), attempt
        except (TransferCancelledError, FatalTransferError):
            raise
        except Exception as exc:
            if is_disk_full_error(exc):
                raise FatalTransferError(f"Destination disk full during {description}: {exc}") from exc
            last_exc = exc
            if attempt + 1 >= attempts:
                break
            logger.warning("Retry %d/%d for %s after error: %s", attempt + 1, attempts - 1, description, exc)
            if on_retry:
                try:
                    on_retry(attempt + 1, exc)
                except Exception as hook_exc:
                    logger.debug("on_retry hook failed for %s: %s", description, hook_exc)
            delay = backoff_base * (2 ** attempt)
            deadline = time.monotonic() + delay
            while time.monotonic() < deadline:
                if cancel_check and cancel_check():
                    raise TransferCancelledError(f"Cancelled while waiting to retry {description}")
                time.sleep(min(0.2, max(0.0, deadline - time.monotonic())))
    assert last_exc is not None
    raise last_exc

# ── Camera Map ────────────────────────────────────────────────────────────────
CAMERAS = {
    "AA": "172.20.114.141", "AB": "172.20.114.142", "AC": "172.20.114.143", "AD": "172.20.114.144",
    "BA": "172.20.114.145", "BB": "172.20.114.146", "BC": "172.20.114.147", "BD": "172.20.114.148",
    "CA": "172.20.114.149", "CB": "172.20.114.150", "CC": "172.20.114.151", "CD": "172.20.114.152",
    "DA": "172.20.114.153", "DB": "172.20.114.154", "DC": "172.20.114.155", "DD": "172.20.114.156",
    "EA": "172.20.114.157", "EB": "172.20.114.158", "EC": "172.20.114.159", "ED": "172.20.114.160",
    "FA": "172.20.114.161", "FB": "172.20.114.162", "FC": "172.20.114.163", "FD": "172.20.114.164",
    "GA": "172.20.114.165", "GB": "172.20.114.166", "GC": "172.20.114.167", "GD": "172.20.114.168",
    "HA": "172.20.114.169", "HB": "172.20.114.170", "HC": "172.20.114.171", "HD": "172.20.114.172",
    "IA": "172.20.114.173", "IB": "172.20.114.174", "IC": "172.20.114.175", "ID": "172.20.114.176",
    "JA": "172.20.114.177", "JB": "172.20.114.178", "JC": "172.20.114.179", "JD": "172.20.114.180",
    "KA": "172.20.114.181", "KB": "172.20.114.182",
}

# Keep a pristine copy so the Networking page can restore factory defaults.
DEFAULT_CAMERAS = dict(CAMERAS)

# Load credentials from env or fall back to defaults. The GUI Networking page can
# override these at runtime and persist them to ~/.mediarunner/network_config.json.
# Existing legacy config/env values are still honored so current testers keep their settings.
FTP_USER = os.environ.get("MEDIARUNNER_FTP_USER") or os.environ.get("FDVC_FTP_USER", "ftp1")
FTP_PASS = os.environ.get("MEDIARUNNER_FTP_PASS") or os.environ.get("FDVC_FTP_PASS", "12345678")
FTP_PORT = int(os.environ.get("MEDIARUNNER_FTP_PORT") or os.environ.get("FDVC_FTP_PORT", "21"))
FTP_TIMEOUT = float(os.environ.get("MEDIARUNNER_FTP_TIMEOUT") or os.environ.get("FDVC_FTP_TIMEOUT", "2.0"))
RCP2_PORT = int(os.environ.get("MEDIARUNNER_RCP2_PORT", "9998"))
RCP2_UDP_PORT = int(os.environ.get("MEDIARUNNER_RCP2_UDP_PORT", "1112"))
_DEFAULT_CONFIG_DIR = Path.home() / ".mediarunner"
_LEGACY_CONFIG_DIR = Path.home() / ".fdvc"
CONFIG_DIR = Path(
    os.environ.get("MEDIARUNNER_CONFIG_DIR")
    or os.environ.get("FDVC_CONFIG_DIR")
    or (_LEGACY_CONFIG_DIR if _LEGACY_CONFIG_DIR.exists() and not _DEFAULT_CONFIG_DIR.exists() else _DEFAULT_CONFIG_DIR)
).expanduser()
NETWORK_CONFIG_PATH = CONFIG_DIR / "network_config.json"


def default_network_config() -> dict:
    return {
        "ftp_user": FTP_USER,
        "ftp_pass": FTP_PASS,
        "ftp_port": FTP_PORT,
        "rcp2_port": RCP2_PORT,
        "rcp2_udp_port": RCP2_UDP_PORT,
        "ftp_timeout": FTP_TIMEOUT,
        "scan_threads": 24,
        "skip_offline": True,
        "finish_sound": True,
        "log_dir": "",
        "redline_path": "",
        "ffmpeg_path": "",
        "ffprobe_path": "",
        "exiftool_path": "",
        "alerts_notify_success": True,
        "alerts_notify_failure": True,
        "alerts_notify_cancelled": True,
        "alerts_email_enabled": False,
        "alerts_smtp_host": "",
        "alerts_smtp_port": 587,
        "alerts_smtp_security": "STARTTLS",
        "alerts_smtp_username": "",
        "alerts_smtp_password": "",
        "alerts_email_from": "",
        "alerts_email_to": "",
        "alerts_email_subject_prefix": "MediaRunner",
        "alerts_gchat_enabled": False,
        "alerts_gchat_webhook_url": "",
        "linux_max_simultaneous_magazines": 6,
        "linux_threads_per_magazine": 1,
        "linux_stage_magazine_subfolders": True,
        "linux_throughput_worker_counts": "1,2,4,6,8,12",
        "linux_throughput_gib_per_worker": 1.0,
        "linux_destination_profiles": {},
        "cameras": dict(DEFAULT_CAMERAS),
    }


def _normalize_network_config(cfg: Optional[dict]) -> dict:
    base = default_network_config()
    if cfg:
        base.update({k: v for k, v in cfg.items() if k != "cameras"})
        if isinstance(cfg.get("cameras"), dict):
            base["cameras"] = {str(k).upper(): str(v).strip() for k, v in cfg["cameras"].items() if str(k).strip()}
    base["ftp_port"] = int(base.get("ftp_port") or 21)
    base["rcp2_port"] = int(base.get("rcp2_port") or 9998)
    base["rcp2_udp_port"] = int(base.get("rcp2_udp_port") or 1112)
    base["ftp_timeout"] = float(base.get("ftp_timeout") or 2.0)
    base["scan_threads"] = max(1, int(base.get("scan_threads") or 24))
    base["skip_offline"] = bool(base.get("skip_offline", True))
    base["finish_sound"] = bool(base.get("finish_sound", True))
    base["linux_max_simultaneous_magazines"] = max(1, min(24, int(base.get("linux_max_simultaneous_magazines") or 6)))
    base["linux_threads_per_magazine"] = max(1, min(8, int(base.get("linux_threads_per_magazine") or 1)))
    base["linux_stage_magazine_subfolders"] = bool(base.get("linux_stage_magazine_subfolders", True))
    try:
        base["linux_throughput_gib_per_worker"] = max(0.1, min(64.0, float(base.get("linux_throughput_gib_per_worker") or 1.0)))
    except (TypeError, ValueError):
        base["linux_throughput_gib_per_worker"] = 1.0
    raw_profiles = base.get("linux_destination_profiles")
    profiles = {}
    if isinstance(raw_profiles, dict):
        for raw_key, raw_profile in raw_profiles.items():
            if not isinstance(raw_profile, dict):
                continue
            profile = dict(raw_profile)
            key = str(profile.get("key") or raw_key or "").strip()
            path = str(profile.get("path") or key or "").strip()
            if not key and path:
                key = path
            if not key:
                continue
            profile["key"] = key
            profile["path"] = path or key
            profile["label"] = str(profile.get("label") or Path(profile["path"]).name or profile["path"]).strip()
            profile["updated_at"] = str(profile.get("updated_at", "") or "").strip()
            try:
                profile["max_simultaneous_magazines"] = max(1, min(24, int(profile.get("max_simultaneous_magazines") or base["linux_max_simultaneous_magazines"])))
            except (TypeError, ValueError):
                profile["max_simultaneous_magazines"] = base["linux_max_simultaneous_magazines"]
            try:
                profile["threads_per_magazine"] = max(1, min(8, int(profile.get("threads_per_magazine") or base["linux_threads_per_magazine"])))
            except (TypeError, ValueError):
                profile["threads_per_magazine"] = base["linux_threads_per_magazine"]
            try:
                profile["throughput_gib_per_worker"] = max(0.1, min(64.0, float(profile.get("throughput_gib_per_worker") or base["linux_throughput_gib_per_worker"])))
            except (TypeError, ValueError):
                profile["throughput_gib_per_worker"] = base["linux_throughput_gib_per_worker"]
            profile["throughput_worker_counts"] = str(profile.get("throughput_worker_counts") or base.get("linux_throughput_worker_counts") or "1,2,4,6,8,12").strip()
            try:
                profile["best_workers"] = max(0, int(profile.get("best_workers") or 0))
            except (TypeError, ValueError):
                profile["best_workers"] = 0
            try:
                profile["peak_bytes_per_second"] = max(0.0, float(profile.get("peak_bytes_per_second") or 0.0))
            except (TypeError, ValueError):
                profile["peak_bytes_per_second"] = 0.0
            clean_results = []
            for row in profile.get("results") or []:
                if not isinstance(row, dict):
                    continue
                try:
                    clean_results.append({
                        "workers": max(1, int(row.get("workers") or 1)),
                        "bytes_written": max(0, int(row.get("bytes_written") or 0)),
                        "elapsed_seconds": max(0.0, float(row.get("elapsed_seconds") or 0.0)),
                        "bytes_per_second": max(0.0, float(row.get("bytes_per_second") or 0.0)),
                        "files_written": max(0, int(row.get("files_written") or 0)),
                        "error": str(row.get("error", "") or ""),
                    })
                except (TypeError, ValueError):
                    continue
            profile["results"] = clean_results[:32]
            profiles[key] = profile
    base["linux_destination_profiles"] = profiles
    for key in ("alerts_notify_success", "alerts_notify_failure", "alerts_notify_cancelled", "alerts_email_enabled", "alerts_gchat_enabled"):
        base[key] = bool(base.get(key, False))
    base["alerts_smtp_port"] = int(base.get("alerts_smtp_port") or 587)
    for key in (
        "redline_path", "ffmpeg_path", "ffprobe_path", "exiftool_path", "log_dir",
        "linux_throughput_worker_counts",
        "alerts_smtp_host", "alerts_smtp_security", "alerts_smtp_username",
        "alerts_smtp_password", "alerts_email_from", "alerts_email_to",
        "alerts_email_subject_prefix", "alerts_gchat_webhook_url",
    ):
        base[key] = str(base.get(key, "") or "").strip()
    return base


def apply_network_config(cfg: dict) -> dict:
    """Apply a network configuration to module globals used by the FTP layer."""
    global CAMERAS, FTP_USER, FTP_PASS, FTP_PORT, FTP_TIMEOUT, RCP2_PORT, RCP2_UDP_PORT
    cfg = _normalize_network_config(cfg)
    CAMERAS.clear()
    CAMERAS.update(cfg["cameras"])
    FTP_USER = str(cfg.get("ftp_user") or FTP_USER)
    FTP_PASS = str(cfg.get("ftp_pass") or FTP_PASS)
    FTP_PORT = int(cfg.get("ftp_port") or 21)
    RCP2_PORT = int(cfg.get("rcp2_port") or 9998)
    RCP2_UDP_PORT = int(cfg.get("rcp2_udp_port") or 1112)
    FTP_TIMEOUT = float(cfg.get("ftp_timeout") or 2.0)
    return cfg


def load_network_config(path: Optional[Path] = None) -> dict:
    """Load persisted FTP/camera settings, falling back to defaults if missing."""
    path = Path(path or NETWORK_CONFIG_PATH).expanduser()
    if not path.exists():
        return apply_network_config(default_network_config())
    try:
        return apply_network_config(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        # Bad config should never prevent the tool from launching on set.
        return apply_network_config(default_network_config())


def save_network_config(cfg: dict, path: Optional[Path] = None) -> Path:
    """Persist FTP/camera settings and apply them immediately."""
    cfg = apply_network_config(cfg)
    path = Path(path or NETWORK_CONFIG_PATH).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write (audit fix #10): never leave a half-written config behind.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, sort_keys=True), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
    return path


def ftp_settings_snapshot(cfg: Optional[dict] = None) -> dict:
    """Immutable per-job copy of FTP settings (audit fix #11).

    Long-running jobs must not observe credential/camera changes made in the
    Networking page mid-transfer. Capture once at job start and pass down.
    """
    if cfg:
        cfg = _normalize_network_config(cfg)
        return {
            "user": str(cfg.get("ftp_user") or FTP_USER),
            "password": str(cfg.get("ftp_pass") or FTP_PASS),
            "port": int(cfg.get("ftp_port") or FTP_PORT),
            "timeout": float(cfg.get("ftp_timeout") or FTP_TIMEOUT),
            "cameras": dict(cfg.get("cameras") or CAMERAS),
        }
    return {
        "user": FTP_USER,
        "password": FTP_PASS,
        "port": FTP_PORT,
        "timeout": FTP_TIMEOUT,
        "cameras": dict(CAMERAS),
    }


# Apply persisted network settings at import time.
load_network_config()


# ── Clip Name Parsing ─────────────────────────────────────────────────────────
def parse_camera_label(filename: str) -> str:
    """
    G007_A083_... → GA
    H007_B081_... → HB
    """
    parts = Path(filename).stem.split("_")
    if len(parts) >= 2 and parts[0] and parts[1]:
        return f"{parts[0][0].upper()}{parts[1][0].upper()}"
    return ""


def parse_clip_numbers(text: str) -> set:
    """'63,64' or '60-64' → {'060','061','062','063','064'}

    Raises ValueError with an operator-readable message on malformed input
    (audit fix #14) instead of an opaque int() traceback in a worker thread.
    """
    clips = set()
    for part in str(text or "").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, _sep, end_text = part.partition("-")
            if not (start_text.strip().isdigit() and end_text.strip().isdigit()):
                raise ValueError(
                    f"Invalid clip selection '{part}'. Use numbers, commas, and ranges like '60-64'."
                )
            start, end = int(start_text), int(end_text)
            if end < start:
                raise ValueError(f"Descending clip range '{part}'. Write it as '{end}-{start}'.")
            for i in range(start, end + 1):
                clips.add(f"{i:03d}")
        else:
            if not part.isdigit():
                raise ValueError(
                    f"Invalid clip selection '{part}'. Use numbers, commas, and ranges like '60-64'."
                )
            clips.add(f"{int(part):03d}")
    return clips


# ── Network ───────────────────────────────────────────────────────────────────
def is_online(ip: str, port: Optional[int] = None, timeout: Optional[float] = None) -> bool:
    """Return True if an FTP socket accepts a connection within the timeout."""
    port = FTP_PORT if port is None else int(port)
    timeout = FTP_TIMEOUT if timeout is None else float(timeout)
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def scan_cameras(cameras: Optional[dict[str, str]] = None, port: Optional[int] = None,
                 timeout: Optional[float] = None, max_workers: int = 24) -> dict[str, bool]:
    """Probe all configured cameras concurrently and return {label: online_bool}."""
    cameras = dict(cameras or CAMERAS)
    port = FTP_PORT if port is None else int(port)
    timeout = FTP_TIMEOUT if timeout is None else float(timeout)
    results: dict[str, bool] = {}
    if not cameras:
        return results
    max_workers = max(1, min(int(max_workers or 1), len(cameras)))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(is_online, ip, port, timeout): label for label, ip in cameras.items()}
        for future in as_completed(futures):
            label = futures[future]
            try:
                results[label] = bool(future.result())
            except Exception:
                results[label] = False
    return dict(sorted(results.items()))


def scan_cameras_detailed(cameras: Optional[dict[str, str]] = None,
                          ftp_port: Optional[int] = None,
                          rcp2_port: Optional[int] = None,
                          timeout: Optional[float] = None,
                          max_workers: int = 24,
                          include_rcp2: bool = True) -> dict[str, dict]:
    """Probe configured cameras and separate FTP readiness from RCP2 visibility.

    FTP remains the transfer gate for camera-array pulls. RCP2 is used for
    identity/diagnostic discovery so an operator can distinguish a RED camera
    that is visible on control port 9998 from one that is actually ready for
    FTP/FTPS media transfer on port 21.
    """
    cameras = dict(CAMERAS if cameras is None else cameras)
    ftp_port = FTP_PORT if ftp_port is None else int(ftp_port)
    rcp2_port = RCP2_PORT if rcp2_port is None else int(rcp2_port)
    timeout = FTP_TIMEOUT if timeout is None else float(timeout)
    if not cameras:
        return {}
    max_workers = max(1, min(int(max_workers or 1), len(cameras)))

    def probe(label: str, ip: str) -> dict:
        detail = {
            "label": str(label),
            "ip": str(ip),
            "ftp_online": False,
            "rcp2_online": False,
            "online": False,
            "transfer_ready": False,
            "method": "OFFLINE",
            "camera_name": "",
            "serial_number": "",
            "camera_version": "",
            "error": "",
        }
        detail["ftp_online"] = is_online(ip, ftp_port, timeout)
        if include_rcp2:
            try:
                from mediarunner_red_wireless import detect_red_camera_identity
                identity = detect_red_camera_identity(
                    ip,
                    port=rcp2_port,
                    timeout=max(1.0, min(float(timeout), 3.0)),
                )
                detail["rcp2_online"] = bool(getattr(identity, "ok", False))
                detail["camera_name"] = str(getattr(identity, "camera_name", "") or "")
                detail["serial_number"] = str(getattr(identity, "serial_number", "") or "")
                detail["camera_version"] = str(getattr(identity, "camera_version", "") or "")
                detail["error"] = "" if detail["rcp2_online"] else str(getattr(identity, "error", "") or "")
            except Exception as exc:
                detail["error"] = str(exc)
        detail["transfer_ready"] = bool(detail["ftp_online"])
        detail["online"] = bool(detail["ftp_online"] or detail["rcp2_online"])
        if detail["ftp_online"] and detail["rcp2_online"]:
            detail["method"] = "FTP+RCP2"
        elif detail["ftp_online"]:
            detail["method"] = "FTP"
        elif detail["rcp2_online"]:
            detail["method"] = "RCP2"
        return detail

    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(probe, label, ip): label for label, ip in cameras.items()}
        for future in as_completed(futures):
            label = futures[future]
            try:
                results[label] = future.result()
            except Exception as exc:
                results[label] = {
                    "label": str(label),
                    "ip": str(cameras.get(label, "")),
                    "ftp_online": False,
                    "rcp2_online": False,
                    "online": False,
                    "transfer_ready": False,
                    "method": "ERROR",
                    "camera_name": "",
                    "serial_number": "",
                    "camera_version": "",
                    "error": str(exc),
                }
    return dict(sorted(results.items()))


def ftp_connect(ip: str, user: Optional[str] = None, password: Optional[str] = None,
                port: Optional[int] = None, timeout: Optional[float] = None) -> FTP_TLS:
    import ssl, sys as _sys
    user = user or FTP_USER
    password = password or FTP_PASS
    port = FTP_PORT if port is None else int(port)
    timeout = 10 if timeout is None else float(timeout)
    # Windows requires an explicit SSL context; macOS/Linux work with defaults
    if _sys.platform == "win32":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ftp = FTP_TLS(context=ctx)
        ftp.connect(ip, port=port, timeout=timeout)
    else:
        ftp = FTP_TLS()
        ftp.connect(ip, port=port, timeout=timeout)
    ftp.login(user, password)
    ftp.prot_p()
    ftp.set_pasv(True)
    return ftp


def ftp_is_dir(ftp: FTP_TLS, path: str) -> bool:
    try:
        ftp.cwd(path)
        ftp.cwd("..")
        return True
    except Exception:
        return False


def ftp_download_dir(ftp: FTP_TLS, remote_dir: str, local_dir: Path,
                     progress_callback: Optional[Callable[[dict], None]] = None,
                     cancel_check: Optional[Callable[[], bool]] = None):
    """Recursively download remote_dir → local_dir, preserving hierarchy."""
    local_dir.mkdir(parents=True, exist_ok=True)
    try:
        items = ftp.nlst(remote_dir)
    except error_perm:
        return
    for item in items:
        name = os.path.basename(item)
        if name in (".", ".."):
            continue
        local_path = local_dir / name
        if ftp_is_dir(ftp, item):
            ftp_download_dir(ftp, item, local_path, progress_callback=progress_callback, cancel_check=cancel_check)
        else:
            _ftp_download_file(ftp, item, local_path, progress_callback=progress_callback, cancel_check=cancel_check)


def _ftp_download_file(ftp: FTP_TLS, remote: str, local: Path,
                       progress_callback: Optional[Callable[[dict], None]] = None,
                       cancel_check: Optional[Callable[[], bool]] = None) -> VerificationResult:
    """Download remote → local.part, resuming a previous partial download when
    the server supports REST (audit fix #2), then verify size and commit."""
    local.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        total = ftp.size(remote) or 0
    except Exception:
        pass
    fname = local.name
    last_emit = [0.0]
    part = _part_path(local)

    resume_offset = 0
    if part.exists():
        existing = part.stat().st_size
        # Resume only when it cannot produce a wrong file: we must know the
        # remote size and the partial must be strictly smaller.
        if total > 0 and 0 < existing < total:
            resume_offset = existing
        else:
            part.unlink()
    transferred = [resume_offset]

    def emit_progress(force: bool = False):
        if progress_callback:
            now = time.monotonic()
            if force or (now - last_emit[0]) >= 0.5:
                last_emit[0] = now
                progress_callback({
                    "remote": remote,
                    "local": str(local),
                    "file": fname,
                    "done": transferred[0],
                    "total": total,
                })
        else:
            _print_progress(fname, transferred[0], total)

    def stream(rest: int):
        mode = "ab" if rest else "wb"
        with part.open(mode) as f:
            try:
                def cb(data):
                    if cancel_check and cancel_check():
                        raise TransferCancelledError(f"Cancelled while downloading {remote}")
                    f.write(data)
                    transferred[0] += len(data)
                    emit_progress()
                ftp.retrbinary(f"RETR {remote}", cb, rest=rest or None)
                _flush_and_fsync(f)
            except Exception:
                try:
                    _flush_and_fsync(f)
                except Exception:
                    pass
                raise

    if resume_offset:
        try:
            logger.info("Resuming %s at byte %d of %d", remote, resume_offset, total)
            stream(resume_offset)
        except (error_perm, error_temp) as exc:
            # Server rejected REST; fall back to a clean full download.
            logger.warning("REST resume rejected for %s (%s); restarting from 0", remote, exc)
            transferred[0] = 0
            if part.exists():
                part.unlink()
            stream(0)
    else:
        stream(0)
    result = verify_local_artifact(
        part,
        expected_size=int(total or 0) if int(total or 0) > 0 else None,
        matched_status=TransferStatus.DOWNLOADED,
        note="Downloaded and locally checksummed",
    )
    if result.status in {TransferStatus.DOWNLOADED, TransferStatus.COPIED, TransferStatus.VERIFIED}:
        commit_part_file(part, local)
    elif result.status == TransferStatus.PARTIAL and part.exists():
        # Keep the .part for a future resume attempt, but never commit it.
        logger.warning("Partial download kept for resume: %s (%s)", part, result.note)
    if progress_callback:
        emit_progress(force=True)
    else:
        print()
    return result


def _print_progress(name: str, done: int, total: int):
    pct = done / total if total else 0
    bar = "#" * int(30 * pct) + "-" * (30 - int(30 * pct))
    print(f"\r  [{bar}] {pct*100:5.1f}%  {name}", end="", flush=True)


# ── Hashing ───────────────────────────────────────────────────────────────────
class TransferStatus:
    PENDING = "Pending"
    COPIED = "Copied"
    DOWNLOADED = "Downloaded"
    DOWNLOADED_LOCAL_CHECKSUMMED = "Downloaded / local-checksummed"
    VERIFIED = "Verified"
    VERIFIED_ASC_MHL = "Verified via ASC MHL"
    SKIPPED_EXISTING_VERIFIED = "Skipped Existing Verified"
    SKIPPED_EXISTING_UNVERIFIED = "Skipped Existing Unverified"
    FAILED = "Failed"
    CANCELLED = "Cancelled"
    MISMATCH = "Mismatch"
    MHL_MISSING = "MHL Missing"
    MHL_MISMATCH = "MHL Mismatch"
    PARTIAL = "Partial"


SUCCESS_TRANSFER_STATUSES = {
    TransferStatus.VERIFIED,
    TransferStatus.VERIFIED_ASC_MHL,
    TransferStatus.SKIPPED_EXISTING_VERIFIED,
}

CAUTION_TRANSFER_STATUSES = {
    TransferStatus.PENDING,
    TransferStatus.COPIED,
    TransferStatus.DOWNLOADED,
    TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED,
    TransferStatus.SKIPPED_EXISTING_UNVERIFIED,
}

FAILURE_TRANSFER_STATUSES = {
    TransferStatus.FAILED,
    TransferStatus.CANCELLED,
    TransferStatus.MISMATCH,
    TransferStatus.MHL_MISSING,
    TransferStatus.MHL_MISMATCH,
    TransferStatus.PARTIAL,
    "FAIL",
    "ERROR",
    "MISSING",
    "Missing",
    "Errors",
    "OFFLINE",
    "NOT FOUND",
}

DEFAULT_CHECKSUM_ALGORITHMS = ("xxh128", "sha256")


class TransferCancelledError(RuntimeError):
    """Raised when a transfer is cancelled mid-stream."""


@dataclass
class VerificationResult:
    status: str
    source_path: str = ""
    destination_path: str = ""
    source_size: int = 0
    destination_size: int = 0
    size_bytes: int = 0
    source_checksums: dict[str, str] = field(default_factory=dict)
    destination_checksums: dict[str, str] = field(default_factory=dict)
    checksum_algorithm: str = ""
    verification_time: str = ""
    error: str = ""
    note: str = ""
    retry_count: int = 0

    @property
    def verification_status(self) -> str:
        return self.status


def _verification_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _part_path(path: Path) -> Path:
    return Path(path).with_name(Path(path).name + ".part")


def _flush_and_fsync(handle) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _normalized_algorithms(
    algorithms: tuple[str, ...] | list[str] | None = None,
    *,
    include_md5: bool = False,
) -> list[str]:
    requested = list(algorithms or DEFAULT_CHECKSUM_ALGORITHMS)
    if include_md5 and "md5" not in requested:
        requested.append("md5")
    normalized: list[str] = []
    for alg in requested:
        name = str(alg or "").strip().lower()
        if not name or name in normalized:
            continue
        if name not in {"xxh128", "xxh64", "sha256", "sha1", "md5"}:
            raise ValueError(f"Unsupported checksum algorithm: {alg}")
        normalized.append(name)
    return normalized


def _new_hasher(algorithm: str):
    name = str(algorithm or "").strip().lower()
    if name == "xxh128":
        try:
            import xxhash  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency is required in normal builds
            raise RuntimeError("xxh128 requested but xxhash is unavailable") from exc
        return xxhash.xxh128()
    if name == "xxh64":
        try:
            import xxhash  # type: ignore
        except Exception as exc:  # pragma: no cover - dependency is required in normal builds
            raise RuntimeError("xxh64 requested but xxhash is unavailable") from exc
        return xxhash.xxh64()
    if name == "sha256":
        return hashlib.sha256()
    if name == "sha1":
        return hashlib.sha1()
    if name == "md5":
        return hashlib.md5()
    raise ValueError(f"Unsupported checksum algorithm: {algorithm}")


def checksum_algorithm_label(checksums: dict[str, str] | tuple[str, ...] | list[str] | None) -> str:
    if isinstance(checksums, dict):
        names = [name for name in ("xxh128", "xxh64", "sha256", "sha1", "md5") if str(checksums.get(name, "")).strip()]
    else:
        names = _normalized_algorithms(list(checksums or ()))
    return ",".join(names)


def transfer_status_bucket(status: object) -> str:
    text = str(status or "").strip()
    if text in SUCCESS_TRANSFER_STATUSES:
        return "ok"
    if text in FAILURE_TRANSFER_STATUSES:
        return "fail"
    return "warn"


def default_verification_source(status: object) -> str:
    text = str(status or "").strip()
    if text == TransferStatus.VERIFIED_ASC_MHL:
        return "asc_mhl"
    if text in {TransferStatus.VERIFIED, TransferStatus.SKIPPED_EXISTING_VERIFIED}:
        return "source_and_destination_checksums"
    if text in {
        TransferStatus.COPIED,
        TransferStatus.DOWNLOADED,
        TransferStatus.DOWNLOADED_LOCAL_CHECKSUMMED,
        TransferStatus.SKIPPED_EXISTING_UNVERIFIED,
    }:
        return "local_checksum"
    if text == TransferStatus.MHL_MISSING:
        return "mhl_required_missing"
    if text in {TransferStatus.MHL_MISMATCH, TransferStatus.MISMATCH}:
        return "mismatch"
    if text == TransferStatus.PARTIAL:
        return "partial"
    if text == TransferStatus.CANCELLED:
        return "cancelled"
    if text == TransferStatus.FAILED:
        return "failed"
    return ""


def compute_checksums(
    path: Path,
    *,
    algorithms: tuple[str, ...] | list[str] | None = None,
    include_md5: bool = False,
    chunk_size: int = 1 << 20,
) -> dict[str, str]:
    path = Path(path)
    hashers = {name: _new_hasher(name) for name in _normalized_algorithms(algorithms, include_md5=include_md5)}
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(int(chunk_size)), b""):
            for hasher in hashers.values():
                hasher.update(chunk)
    return {name: hasher.hexdigest() for name, hasher in hashers.items()}


def copy_file_to_part(
    src: Path,
    dst: Path,
    *,
    progress_callback: Optional[Callable[[int], None]] = None,
    chunk_size: int = 8 * 1024 * 1024,
    preserve_metadata: bool = True,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Path:
    part, _checksums = copy_file_to_part_with_hash(
        src,
        dst,
        algorithms=(),
        progress_callback=progress_callback,
        chunk_size=chunk_size,
        preserve_metadata=preserve_metadata,
        cancel_check=cancel_check,
    )
    return part


def copy_file_to_part_with_hash(
    src: Path,
    dst: Path,
    *,
    algorithms: tuple[str, ...] | list[str] | None = None,
    progress_callback: Optional[Callable[[int], None]] = None,
    chunk_size: int = 8 * 1024 * 1024,
    preserve_metadata: bool = True,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> tuple[Path, dict[str, str]]:
    """Copy src → dst.part while hashing the source stream in the same read
    (audit fix #7). Returns (part_path, source_checksums).

    Hashing during the copy removes a full extra read of the source per
    destination: verification then only needs to hash the destination.
    Pass algorithms=() to skip hashing entirely.
    """
    src = Path(src)
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = _part_path(dst)
    if part.exists():
        part.unlink()
    if algorithms is None:
        algorithms = DEFAULT_CHECKSUM_ALGORITHMS
    hashers = {name: _new_hasher(name) for name in _normalized_algorithms(list(algorithms))} if algorithms else {}
    with src.open("rb") as reader, part.open("wb") as writer:
        try:
            while True:
                if cancel_check and cancel_check():
                    raise TransferCancelledError(f"Cancelled while copying {src.name}")
                chunk = reader.read(int(chunk_size))
                if not chunk:
                    break
                writer.write(chunk)
                for hasher in hashers.values():
                    hasher.update(chunk)
                if progress_callback is not None:
                    try:
                        progress_callback(len(chunk))
                    except Exception:
                        pass
            _flush_and_fsync(writer)
        except Exception:
            try:
                _flush_and_fsync(writer)
            except Exception:
                pass
            raise
    if preserve_metadata:
        try:
            shutil.copystat(src, part, follow_symlinks=True)
        except Exception as exc:
            logger.debug("copystat failed for %s: %s", part, exc)
    return part, {name: hasher.hexdigest() for name, hasher in hashers.items()}


def cleanup_stale_parts(root: Path, *, min_age_seconds: float = 60.0) -> int:
    """Remove orphaned .part files under root (audit fix #9).

    Only deletes files older than min_age_seconds so an active job writing
    into the same tree is never disturbed. Returns the number removed.
    """
    root = Path(root)
    if not root.exists():
        return 0
    removed = 0
    now = time.time()
    try:
        for part in root.rglob("*.part"):
            try:
                if not part.is_file():
                    continue
                if (now - part.stat().st_mtime) < float(min_age_seconds):
                    continue
                part.unlink()
                removed += 1
                logger.info("Removed stale partial file: %s", part)
            except OSError as exc:
                logger.debug("Could not remove stale part %s: %s", part, exc)
    except OSError as exc:
        logger.debug("Stale .part sweep failed under %s: %s", root, exc)
    return removed


def commit_part_file(part: Path, dst: Path) -> Path:
    part = Path(part)
    dst = Path(dst)
    part.replace(dst)
    return dst


def verify_file_pair(
    source_path: Path,
    destination_path: Path,
    *,
    algorithms: tuple[str, ...] | list[str] | None = None,
    include_md5: bool = False,
    source_checksums: Optional[dict[str, str]] = None,
) -> VerificationResult:
    source = Path(source_path)
    destination = Path(destination_path)
    result = VerificationResult(
        status=TransferStatus.FAILED,
        source_path=str(source),
        destination_path=str(destination),
        verification_time=_verification_timestamp(),
    )
    if not source.exists():
        result.error = f"Missing source file: {source}"
        result.note = result.error
        return result
    if not destination.exists():
        result.error = f"Missing destination file: {destination}"
        result.note = result.error
        return result

    result.source_size = source.stat().st_size
    result.destination_size = destination.stat().st_size
    result.size_bytes = result.destination_size
    normalized = _normalized_algorithms(algorithms, include_md5=include_md5)
    result.checksum_algorithm = checksum_algorithm_label(normalized)

    if result.source_size != result.destination_size:
        result.status = TransferStatus.MISMATCH
        result.note = f"Size mismatch: source {result.source_size}, destination {result.destination_size}"
        return result

    result.source_checksums = dict(source_checksums or compute_checksums(source, algorithms=normalized))
    result.destination_checksums = compute_checksums(destination, algorithms=normalized)
    mismatched = [
        name for name in normalized
        if str(result.source_checksums.get(name, "")) != str(result.destination_checksums.get(name, ""))
    ]
    if mismatched:
        result.status = TransferStatus.MISMATCH
        result.note = f"Checksum mismatch: {', '.join(mismatched)}"
        result.error = result.note
    else:
        result.status = TransferStatus.VERIFIED
        result.note = "Source and destination verified"
    return result


def assess_existing_destination(
    source_path: Path,
    destination_path: Path,
    *,
    algorithms: tuple[str, ...] | list[str] | None = None,
    include_md5: bool = False,
) -> VerificationResult:
    result = verify_file_pair(
        source_path,
        destination_path,
        algorithms=algorithms,
        include_md5=include_md5,
    )
    if result.status == TransferStatus.VERIFIED:
        result.status = TransferStatus.SKIPPED_EXISTING_VERIFIED
        result.note = "Existing destination verified by policy"
    else:
        result.status = TransferStatus.SKIPPED_EXISTING_UNVERIFIED
        result.note = result.note or "Existing destination failed verification policy"
    return result


def verify_local_artifact(
    path: Path,
    *,
    expected_size: int | None = None,
    algorithms: tuple[str, ...] | list[str] | None = None,
    include_md5: bool = False,
    matched_status: str = TransferStatus.DOWNLOADED,
    note: str = "Local artifact verified",
) -> VerificationResult:
    target = Path(path)
    result = VerificationResult(
        status=TransferStatus.FAILED,
        destination_path=str(target),
        verification_time=_verification_timestamp(),
        source_size=int(expected_size or 0),
    )
    if not target.exists():
        result.error = f"Missing local artifact: {target}"
        result.note = result.error
        return result

    result.destination_size = target.stat().st_size
    result.size_bytes = result.destination_size
    normalized = _normalized_algorithms(algorithms, include_md5=include_md5)
    result.checksum_algorithm = checksum_algorithm_label(normalized)
    result.destination_checksums = compute_checksums(target, algorithms=normalized)
    if expected_size is not None and int(expected_size) > 0 and result.destination_size != int(expected_size):
        result.status = TransferStatus.PARTIAL
        result.note = f"Size mismatch after transfer: expected {int(expected_size)}, got {result.destination_size}"
        result.error = result.note
        return result
    result.status = matched_status
    result.note = note
    return result


def verification_result_to_manifest_kwargs(result: VerificationResult, **extra) -> dict[str, object]:
    row = dict(extra)
    status = str(row.get("verification_status") or row.get("status") or result.status or "").strip()
    status = status or result.status
    note_parts = [str(result.note or "").strip(), str(row.get("note", "") or "").strip()]
    error_text = str(row.get("error", "") or result.error or "").strip()
    xxhash_value = (
        str(row.get("xxhash", "") or "")
        or str(result.destination_checksums.get("xxh128", "") or result.source_checksums.get("xxh128", ""))
    )
    sha256_value = (
        str(row.get("sha256", "") or "")
        or str(result.destination_checksums.get("sha256", "") or result.source_checksums.get("sha256", ""))
    )
    md5_value = (
        str(row.get("md5", "") or "")
        or str(result.destination_checksums.get("md5", "") or result.source_checksums.get("md5", ""))
    )
    row.update({
        "status": status,
        "verification_status": status,
        "source_path": str(row.get("source_path", "") or result.source_path),
        "destination_path": str(row.get("destination_path", "") or result.destination_path),
        "source_size": str(row.get("source_size", "") or result.source_size or ""),
        "destination_size": str(row.get("destination_size", "") or result.destination_size or ""),
        "size_bytes": str(row.get("size_bytes", "") or result.size_bytes or result.destination_size or result.source_size or ""),
        "size_human": str(
            row.get("size_human", "")
            or (human_size(int(result.size_bytes or result.destination_size or result.source_size)) if int(result.size_bytes or result.destination_size or result.source_size or 0) > 0 else "")
        ),
        "src_hash": str(row.get("src_hash", "") or result.source_checksums.get("xxh128", "")),
        "dst_hash": str(row.get("dst_hash", "") or result.destination_checksums.get("xxh128", "")),
        "xxhash": xxhash_value,
        "md5": md5_value,
        "sha256": sha256_value,
        "checksum_algorithm": str(row.get("checksum_algorithm", "") or result.checksum_algorithm or checksum_algorithm_label({
            "xxh128": xxhash_value,
            "sha256": sha256_value,
            "md5": md5_value,
        })),
        "verification_time": str(row.get("verification_time", "") or result.verification_time or _verification_timestamp()),
        "verification_source": str(row.get("verification_source", "") or default_verification_source(status)),
        "mhl_path": str(row.get("mhl_path", "") or ""),
        "mhl_algorithm": str(row.get("mhl_algorithm", "") or ""),
        "mhl_expected_hash": str(row.get("mhl_expected_hash", "") or ""),
        "mhl_actual_hash": str(row.get("mhl_actual_hash", "") or ""),
        "mhl_verified": str(row.get("mhl_verified", "") or ""),
        "retry_count": str(row.get("retry_count", "") or result.retry_count or "0"),
        "error": error_text,
        "note": "; ".join(part for part in note_parts if part),
    })
    return row


_warned_xxhash_fallback = False


def xxh128(path: Path) -> str:
    global _warned_xxhash_fallback
    try:
        return compute_checksums(Path(path), algorithms=("xxh128",)).get("xxh128", "")
    except RuntimeError as exc:
        # xxhash module unavailable (audit fix #15): fall back to a clearly
        # labelled sha256 so values never silently masquerade as xxh128, and
        # warn loudly once so the build problem is visible.
        if not _warned_xxhash_fallback:
            _warned_xxhash_fallback = True
            logger.error("xxhash unavailable, falling back to sha256 hashes: %s", exc)
        return f"sha256:{compute_checksums(Path(path), algorithms=('sha256',)).get('sha256', '')}"


# ── Manifest ──────────────────────────────────────────────────────────────────
MANIFEST_FIELDS = [
    "timestamp",
    "verification_time",
    "method",
    "source_path",
    "destination_path",
    "camera",
    "reel",
    "clip",
    "file",
    "source_size",
    "destination_size",
    "size_bytes",
    "size_human",
    "src_hash",
    "dst_hash",
    "xxhash",
    "md5",
    "sha256",
    "checksum_algorithm",
    "status",
    "verification_status",
    "verification_source",
    "mhl_path",
    "mhl_algorithm",
    "mhl_expected_hash",
    "mhl_actual_hash",
    "mhl_verified",
    "retry_count",
    "error",
    "note",
]


class Manifest:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            with open(path, "w", newline="") as f:
                csv.DictWriter(f, fieldnames=MANIFEST_FIELDS).writeheader()

    def write(self, **kwargs):
        # Backwards compatibility: older internal modules still pass stage="Transfer"/"FTP"/"Meta".
        if "stage" in kwargs and "method" not in kwargs:
            kwargs["method"] = kwargs.pop("stage")
        elif "stage" in kwargs:
            kwargs.pop("stage", None)
        status = str(kwargs.get("verification_status") or kwargs.get("status") or "").strip()
        if status:
            kwargs.setdefault("status", status)
            kwargs.setdefault("verification_status", status)
            kwargs.setdefault("verification_time", _verification_timestamp())
        kwargs.setdefault("verification_source", default_verification_source(kwargs.get("verification_status") or kwargs.get("status")))
        size_bytes = _manifest_size_bytes(kwargs.get("size_bytes") or kwargs.get("destination_size") or kwargs.get("source_size"))
        if size_bytes > 0:
            kwargs.setdefault("size_bytes", str(size_bytes))
            kwargs.setdefault("size_human", human_size(size_bytes))
        kwargs.setdefault("retry_count", str(kwargs.get("retry_count") or "0"))
        if not kwargs.get("xxhash"):
            kwargs["xxhash"] = str(kwargs.get("dst_hash") or kwargs.get("src_hash") or "")
        if not kwargs.get("checksum_algorithm"):
            kwargs["checksum_algorithm"] = checksum_algorithm_label({
                "xxh128": str(kwargs.get("xxhash") or kwargs.get("dst_hash") or kwargs.get("src_hash") or ""),
                "sha256": str(kwargs.get("sha256") or ""),
                "md5": str(kwargs.get("md5") or ""),
            })
        if not kwargs.get("note") and kwargs.get("error"):
            kwargs["note"] = kwargs.get("error")
        row = {k: "" for k in MANIFEST_FIELDS}
        row["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for k, v in kwargs.items():
            if k in row:
                row[k] = v
        with open(self.path, "a", newline="") as f:
            csv.DictWriter(f, fieldnames=MANIFEST_FIELDS).writerow(row)


def _manifest_size_bytes(value) -> int:
    try:
        return int(str(value or "").strip())
    except Exception:
        return 0


def _ftp_manifest_local_path(output_dir: Path, row: dict) -> Optional[Path]:
    camera = str(row.get("camera") or "").strip()
    reel = str(row.get("reel") or "").strip()
    clip = str(row.get("clip") or "").strip()
    file_name = str(row.get("file") or "").strip()
    if not all((camera, reel, clip, file_name)):
        return None
    return Path(output_dir) / camera / reel / clip / file_name


def finalize_ftp_manifest(
    manifest_csv: Path,
    output_dir: Path,
    cameras: Optional[dict[str, str]] = None,
    destination_role: str = "",
    project: str = "MediaRunner FTP",
) -> Optional[Path]:
    """
    Normalize and enrich an FTP manifest, then write a sibling HTML report.
    Returns the HTML report path when the manifest exists and was processed.
    """
    manifest_csv = Path(manifest_csv)
    if not manifest_csv.exists():
        return None

    camera_map = {str(k).upper(): str(v).strip() for k, v in dict(cameras or CAMERAS).items()}
    with open(manifest_csv, newline="") as f:
        source_rows = list(csv.DictReader(f))

    normalized_rows: list[dict[str, str]] = []
    for raw_row in source_rows:
        row = {field: str(raw_row.get(field, "") or "") for field in MANIFEST_FIELDS}
        if not row["method"]:
            row["method"] = str(raw_row.get("stage") or "").strip() or "FTP"
        if not row["verification_status"]:
            row["verification_status"] = row["status"]
        if not row["status"]:
            row["status"] = row["verification_status"]
        if not row["verification_source"]:
            row["verification_source"] = default_verification_source(row["verification_status"] or row["status"])
        if not row["note"] and destination_role:
            row["note"] = destination_role

        local_file = _ftp_manifest_local_path(output_dir, row)
        if local_file is not None and not row["destination_path"]:
            row["destination_path"] = str(local_file)

        camera_ip = camera_map.get(str(row.get("camera") or "").upper())
        if camera_ip and not row["source_path"]:
            reel = str(row.get("reel") or "").strip()
            clip = str(row.get("clip") or "").strip()
            file_name = str(row.get("file") or "").strip()
            if reel and clip and file_name:
                row["source_path"] = f"ftps://{camera_ip}/media/{reel}/{clip}/{file_name}"

        if local_file is not None and local_file.exists() and local_file.is_file():
            size_bytes = local_file.stat().st_size
            row["destination_size"] = row["destination_size"] or str(size_bytes)
            row["source_size"] = row["source_size"] or row["size_bytes"] or str(size_bytes)
            if not row["size_bytes"] or _manifest_size_bytes(row["size_bytes"]) <= 0:
                row["size_bytes"] = str(size_bytes)
            if not row["size_human"]:
                row["size_human"] = human_size(size_bytes)
            if not row["xxhash"] and row["dst_hash"]:
                row["xxhash"] = row["dst_hash"]
        elif row["size_bytes"] and not row["size_human"]:
            row["size_human"] = human_size(_manifest_size_bytes(row["size_bytes"]))
        if not row["checksum_algorithm"]:
            row["checksum_algorithm"] = checksum_algorithm_label({
                "xxh128": row["xxhash"] or row["dst_hash"] or row["src_hash"],
                "sha256": row["sha256"],
                "md5": row["md5"],
            })
        if not row["verification_time"] and (row["status"] or row["verification_status"]):
            row["verification_time"] = row["timestamp"]

        normalized_rows.append(row)

    # Atomic rewrite (audit fix #10): a crash mid-rewrite must not destroy the
    # job's audit trail. Write a sibling temp file, then replace.
    tmp_csv = manifest_csv.with_name(manifest_csv.name + ".tmp")
    with open(tmp_csv, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=MANIFEST_FIELDS)
        writer.writeheader()
        writer.writerows(normalized_rows)
        f.flush()
        os.fsync(f.fileno())
    tmp_csv.replace(manifest_csv)

    report_path = manifest_csv.with_suffix(".html")
    write_html_report(
        manifest_csv,
        project=project,
        out_html=report_path,
        destination_path=str(Path(output_dir)),
        method_label="FTP",
    )
    return report_path


# ── HTML Report ───────────────────────────────────────────────────────────────

# Single source of truth for the MediaRunner brand mark in HTML artifacts.
BRAND_MARK_HTML = (
    '<div style="width:46px;height:46px;border-radius:9px;'
    'background:linear-gradient(135deg,#5AC8E6,#2D7FA0);'
    'display:flex;align-items:center;justify-content:center;'
    'font-weight:900;color:#06121A;font-size:18px;'
    'font-family:Helvetica,Arial,sans-serif;flex:none">MR</div>'
)


def write_html_report(manifest_csv: Path, project: str, out_html: Path,
                      source_path: str = "", destination_path: str = "",
                      method_label: str = "Transfer"):
    rows = []
    with open(manifest_csv, newline="") as f:
        rows = list(csv.DictReader(f))

    ok = sum(1 for r in rows if transfer_status_bucket(r.get("verification_status") or r.get("status")) == "ok")
    warn = sum(1 for r in rows if transfer_status_bucket(r.get("verification_status") or r.get("status")) == "warn")
    fail = sum(1 for r in rows if transfer_status_bucket(r.get("verification_status") or r.get("status")) == "fail")
    total_bytes = sum(int(r.get("size_bytes", "0")) for r in rows if str(r.get("size_bytes", "")).isdigit())
    total_human = human_size(total_bytes)

    if not source_path:
        source_path = next((r.get("source_path", "") for r in rows if r.get("source_path")), "")
    if not destination_path:
        destination_path = next((r.get("destination_path", "") for r in rows if r.get("destination_path")), "")
    if not method_label:
        method_label = next((r.get("method", "") or r.get("stage", "") for r in rows if r.get("method") or r.get("stage")), "Transfer")

    # 0.3 brand: the MR mark renders identically in the app sidebar, this
    # report, FTP reports, and custom reports — one mark across all artifacts.
    logo_tag = BRAND_MARK_HTML

    def esc(value):
        import html
        return html.escape(str(value or ""))

    def cell(value):
        # No empty cells in deliverables: blank metadata renders as an em dash.
        text = str(value or "").strip()
        return esc(text) if text else "&mdash;"

    def row_html(r):
        status = r.get("verification_status", "") or r.get("status", "")
        cls = transfer_status_bucket(status)
        size_raw = r.get("size_bytes", "")
        size_h = human_size(int(size_raw)) if str(size_raw).isdigit() else r.get("size_human", size_raw)
        method = r.get("method", "") or r.get("stage", "")
        return (
            f'<tr class="{cls}">'
            f'<td>{cell(status)}</td><td>{cell(method)}</td>'
            f'<td>{cell(r.get("camera",""))}</td><td>{cell(r.get("reel",""))}</td>'
            f'<td>{cell(r.get("clip",""))}</td><td>{cell(r.get("file",""))}</td>'
            f'<td style="white-space:nowrap">{cell(size_h)}</td>'
            f'<td class="hash">{cell(r.get("src_hash",""))}</td>'
            f'<td class="hash">{cell(r.get("dst_hash",""))}</td>'
            f'<td>{cell(r.get("note",""))}</td>'
            f'</tr>'
        )

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>MediaRunner Report – {esc(project)}</title>
<style>
  body{{font-family:Helvetica,sans-serif;margin:40px;color:#222;font-size:13px}}
  .header{{border-bottom:4px solid #000;padding-bottom:16px;margin-bottom:24px;display:flex;align-items:center;gap:20px}}
  .logo{{height:80px;object-fit:contain}}
  h1{{font-size:28px;margin:0}} h2{{font-size:16px;color:#666;margin:4px 0 0}}
  .summary{{background:#f5f5f5;border:1px solid #ddd;border-radius:6px;padding:16px;margin-bottom:24px;line-height:1.8}}
  .path{{font-family:monospace;font-size:12px;color:#444;word-break:break-all}}
  table{{width:100%;border-collapse:collapse;table-layout:fixed}}
  th{{background:#222;color:#fff;padding:10px;text-align:left;font-size:12px}}
  td{{border-bottom:1px solid #e0e0e0;padding:9px;font-size:11px;word-break:break-all}}
  tr.ok td:first-child{{color:#28a745;font-weight:bold}}
  tr.warn td:first-child{{color:#d9a441;font-weight:bold}}
  tr.fail td:first-child{{color:#dc3545;font-weight:bold}}
  .hash{{font-family:monospace;font-size:10px;color:#555}}
</style></head><body>
<div class="header">{logo_tag}<div><h1>MediaRunner Transfer Report</h1><h2>Project: {esc(project)}</h2></div></div>
<div class="summary">
  <strong>Generated:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M")}<br>
  <strong>Method:</strong> {esc(method_label)}<br>
  <strong>Source:</strong> <span class="path">{esc(source_path)}</span><br>
  <strong>Destination:</strong> <span class="path">{esc(destination_path)}</span><br>
  <strong>Total Files:</strong> {len(rows)} &nbsp;|&nbsp;
  <strong>Total Data:</strong> {total_human} &nbsp;|&nbsp;
  <strong style="color:#28a745">Verified: {ok}</strong> &nbsp;|&nbsp;
  <strong style="color:#d9a441">Unverified: {warn}</strong> &nbsp;|&nbsp;
  <strong style="color:#dc3545">FAIL: {fail}</strong>
</div>
<table>
<thead><tr>
  <th style="width:7%">Status</th><th style="width:8%">Method</th>
  <th style="width:5%">Cam</th><th style="width:8%">Reel</th>
  <th style="width:10%">Clip</th><th style="width:13%">File</th>
  <th style="width:7%">Size</th>
  <th style="width:17%">Src Hash</th><th style="width:17%">Dst Hash</th>
  <th style="width:8%">Note</th>
</tr></thead><tbody>
{''.join(row_html(r) for r in rows)}
</tbody>
<tfoot>
  <tr style="background:#f0f0f0; font-weight:bold; font-size:12px;">
    <td colspan="6" style="padding:10px; text-align:right; color:#333;">TOTAL TRANSFERRED</td>
    <td style="padding:10px; white-space:nowrap; color:#222;">{total_human}</td>
    <td colspan="3" style="padding:10px; color:#333;">{ok} verified &nbsp;·&nbsp; {warn} unverified &nbsp;·&nbsp; {fail} failed &nbsp;·&nbsp; {len(rows)} files</td>
  </tr>
</tfoot></table>
</body></html>"""

    out_html.write_text(html, encoding="utf-8")
    return ok, fail


# ── Completion sound ─────────────────────────────────────────────────────────
def play_completion_sound():
    """Completion sound intentionally disabled for public-facing builds."""
    return

# ── Human-readable size ───────────────────────────────────────────────────────
def human_size(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1024:
            return f"{b:.2f} {unit}"
        b /= 1024
    return f"{b:.2f} PB"
