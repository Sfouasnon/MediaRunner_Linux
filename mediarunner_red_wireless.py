#!/usr/bin/env python3
"""
MediaRunner RED Wireless Ingest support.

RCP2 is used for camera identification/diagnostics. RED media is discovered and
copied over FTP/FTPS, matching RED's camera media-access workflow.
"""
from __future__ import annotations

import base64
import csv
import ftplib
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import ssl
import struct
import subprocess
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Optional

from mediarunner_core import (
    Manifest,
    human_size,
    xxh128,
    write_html_report,
    TransferStatus,
    assess_existing_destination,
    verify_file_pair,
    verify_local_artifact,
    verification_result_to_manifest_kwargs,
    VerificationResult,
    retry_operation,
    TransferCancelledError,  # noqa: F401  (raised through retry_operation)
)
from mediarunner_transfer import copy2_with_progress

logger = logging.getLogger("mediarunner.red_wireless")

DEFAULT_RCP2_PORT = 9998
DEFAULT_FTPS_PORT = 21
DEFAULT_FTP_USERNAME = "ftp1"
DEFAULT_FTP_PASSWORD = "12345678"
DEFAULT_TIMEOUT_SECONDS = 6.0
DEFAULT_REMOTE_ROOTS = ("/media", "/media/data", "/")
RCP2_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


@dataclass
class RedCameraIdentity:
    ok: bool
    host: str
    port: int = DEFAULT_RCP2_PORT
    camera_name: str = ""
    serial_number: str = ""
    camera_version: str = ""
    raw: dict | None = None
    error: str = ""


@dataclass
class RedWirelessMediaFile:
    remote_path: str
    relative_path: str
    size_bytes: int = 0
    camera: str = ""
    reel: str = ""
    clip: str = ""
    file_name: str = ""


@dataclass
class RedWirelessDiscovery:
    ok: bool
    host: str
    protocol: str
    remote_root: str = ""
    reel: str = ""
    clip_spec: str = ""
    files: list[RedWirelessMediaFile] | None = None
    total_bytes: int = 0
    error: str = ""
    log_lines: list[str] | None = None

    @property
    def file_count(self) -> int:
        return len(self.files or [])


@dataclass
class RedWirelessIngestResult:
    ok: bool
    ok_count: int
    fail_count: int
    manifest_paths: list[str]
    report_paths: list[str]


# ── RCP2 WebSocket identity probe ────────────────────────────────────────────

def _ws_expected_accept(key: str) -> str:
    digest = hashlib.sha1(f"{key}{RCP2_WS_GUID}".encode("utf-8")).digest()
    return base64.b64encode(digest).decode("ascii")


def _ws_upgrade(host: str, port: int, timeout: float) -> socket.socket:
    sock = socket.create_connection((host, int(port)), timeout=float(timeout))
    sock.settimeout(float(timeout))
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET / HTTP/1.1\r\n"
        f"Host: {host}:{int(port)}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    ).encode("utf-8")
    sock.sendall(request)
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    text = response.decode("utf-8", errors="replace")
    status_line = text.split("\r\n", 1)[0]
    headers: dict[str, str] = {}
    for line in text.split("\r\n")[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    if "101" not in status_line or headers.get("sec-websocket-accept", "") != _ws_expected_accept(key):
        try:
            sock.close()
        finally:
            raise RuntimeError(f"WebSocket upgrade failed: {status_line or text[:120]}")
    return sock


def _ws_text_frame(text: str) -> bytes:
    payload = text.encode("utf-8")
    mask_key = os.urandom(4)
    header = bytearray([0x81])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", n))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", n))
    header.extend(mask_key)
    masked = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return bytes(header) + masked


def _ws_send_json(sock: socket.socket, payload: dict) -> None:
    sock.sendall(_ws_text_frame(json.dumps(payload, separators=(",", ":"))))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    out = b""
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise EOFError("WebSocket closed")
        out += chunk
    return out


def _ws_recv_text(sock: socket.socket, timeout: float) -> Optional[str]:
    sock.settimeout(float(timeout))
    fragments: list[bytes] = []
    while True:
        header = _recv_exact(sock, 2)
        fin = (header[0] >> 7) & 1
        opcode = header[0] & 0x0F
        masked = (header[1] >> 7) & 1
        payload_len = header[1] & 0x7F
        if payload_len == 126:
            payload_len = struct.unpack("!H", _recv_exact(sock, 2))[0]
        elif payload_len == 127:
            payload_len = struct.unpack("!Q", _recv_exact(sock, 8))[0]
        mask = _recv_exact(sock, 4) if masked else b""
        payload = _recv_exact(sock, payload_len) if payload_len else b""
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            # pong
            sock.sendall(bytes([0x8A, 0x80, 0, 0, 0, 0]))
            continue
        if opcode in (0x1, 0x0):
            fragments.append(payload)
            if fin:
                return b"".join(fragments).decode("utf-8", errors="replace")
            continue
        raise RuntimeError(f"Unsupported WebSocket opcode {opcode}")


def _read_matching_json(sock: socket.socket, matcher: Callable[[dict], bool], timeout: float, settle: float = 0.25) -> list[dict]:
    deadline = time.monotonic() + float(timeout)
    matches: list[dict] = []
    idle_deadline: float | None = None
    while time.monotonic() < deadline:
        wait = max(0.05, min(deadline - time.monotonic(), settle if idle_deadline else float(timeout)))
        try:
            text = _ws_recv_text(sock, wait)
        except socket.timeout:
            if matches:
                break
            raise
        if text is None:
            break
        try:
            payload = json.loads(text)
        except Exception:
            continue
        if isinstance(payload, dict) and matcher(payload):
            matches.append(payload)
            idle_deadline = time.monotonic() + settle
        if idle_deadline and time.monotonic() >= idle_deadline:
            break
    if not matches:
        raise TimeoutError("No matching RCP2 message received")
    return matches


def _camera_info_summary(payload: dict) -> dict:
    camera_type = dict(payload.get("camera_type") or {})
    version = dict(payload.get("version") or {})
    return {
        "camera_name": str(payload.get("name") or payload.get("camera_name") or ""),
        "camera_type": str(camera_type.get("str") or camera_type.get("num") or payload.get("camera_type") or ""),
        "serial_number": str(payload.get("serial_number") or payload.get("serial") or ""),
        "camera_version": str(version.get("str") or payload.get("camera_version") or ""),
        "raw": payload,
    }


def detect_red_camera_identity(host: str, *, port: int = DEFAULT_RCP2_PORT, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> RedCameraIdentity:
    """Best-effort RCP2 identity probe over WebSocket."""
    host = str(host or "").strip()
    if not host:
        return RedCameraIdentity(ok=False, host=host, port=int(port), error="Camera IP is empty")
    sock: socket.socket | None = None
    try:
        sock = _ws_upgrade(host, int(port), float(timeout))
        _ws_send_json(sock, {"type": "rcp_config", "strings_decoded": 1, "json_minified": 1, "encoding_type": "legacy", "client": {"name": "MediaRunner", "version": "0.2"}})
        _read_matching_json(sock, lambda m: str(m.get("type") or "") == "rcp_config", timeout=float(timeout), settle=0.15)
        _ws_send_json(sock, {"type": "rcp_get_types"})
        try:
            _read_matching_json(sock, lambda m: str(m.get("type") or "") == "rcp_cur_types", timeout=float(timeout), settle=0.15)
        except Exception:
            pass
        _ws_send_json(sock, {"type": "rcp_get", "id": "CAMERA_INFO"})
        messages = _read_matching_json(sock, lambda m: str(m.get("type") or "") == "rcp_cur_cam_info", timeout=float(timeout), settle=0.2)
        summary = _camera_info_summary(messages[-1])
        return RedCameraIdentity(
            ok=True,
            host=host,
            port=int(port),
            camera_name=summary["camera_name"],
            serial_number=summary["serial_number"],
            camera_version=summary["camera_version"],
            raw=summary,
        )
    except Exception as exc:
        return RedCameraIdentity(ok=False, host=host, port=int(port), error=str(exc))
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass


# ── Network scan / camera discovery ─────────────────────────────────────────

def _normalize_netmask(mask_token: str) -> str:
    token = str(mask_token or "").strip()
    if not token:
        return ""
    if token.startswith("0x"):
        try:
            value = int(token, 16)
            return socket.inet_ntoa(value.to_bytes(4, byteorder="big"))
        except Exception:
            return token
    return token


def _mac_hardware_port_map() -> dict[str, str]:
    """Return interface -> human hardware port label on macOS when available."""
    try:
        result = subprocess.run(
            ["/usr/sbin/networksetup", "-listallhardwareports"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return {}
    mapping: dict[str, str] = {}
    hardware_port = ""
    for raw in result.stdout.splitlines():
        line = raw.strip()
        if line.startswith("Hardware Port:"):
            hardware_port = line.split(":", 1)[1].strip()
        elif line.startswith("Device:"):
            device = line.split(":", 1)[1].strip()
            if device and hardware_port:
                mapping[device] = hardware_port
    return mapping


def _network_interfaces_for_scan() -> list[dict[str, object]]:
    """Best-effort IPv4 interface discovery.

    RED Control-style detection works by probing reachable hosts on active local
    interfaces. Prefer Wi-Fi interfaces first, but include other active local
    interfaces because RED's FTPS/RCP2 network stack is identical over USB-C
    Ethernet and link-local adapter configurations.
    """
    interfaces: list[dict[str, object]] = []
    hardware_ports = _mac_hardware_port_map()
    try:
        result = subprocess.run(["/sbin/ifconfig"], check=True, capture_output=True, text=True, timeout=2)
    except Exception:
        result = None
    if result is not None:
        current: dict[str, object] | None = None
        for raw in result.stdout.splitlines():
            if raw and not raw[0].isspace():
                if current is not None and current.get("ipv4_address"):
                    interfaces.append(current)
                name = raw.split(":", 1)[0].strip()
                current = {
                    "name": name,
                    "label": hardware_ports.get(name, name),
                    "ipv4_address": "",
                    "subnet_mask": "",
                    "is_wifi": "wi-fi" in hardware_ports.get(name, name).lower() or "airport" in hardware_ports.get(name, name).lower(),
                    "is_link_local": False,
                }
                continue
            if current is None:
                continue
            line = raw.strip()
            if not line.startswith("inet "):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            address = parts[1].strip()
            if address.startswith("127."):
                continue
            mask = ""
            if "netmask" in parts:
                try:
                    mask = _normalize_netmask(parts[parts.index("netmask") + 1])
                except Exception:
                    mask = ""
            current["ipv4_address"] = address
            current["subnet_mask"] = mask
            current["is_link_local"] = address.startswith("169.254.")
        if current is not None and current.get("ipv4_address"):
            interfaces.append(current)
    if not interfaces:
        # Portable fallback. This only gives an address, but it is still useful
        # for laptops without ifconfig output in sandboxed/packaged contexts.
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            probe.close()
            if not address.startswith("127."):
                interfaces.append({"name": "default", "label": "Default Network", "ipv4_address": address, "subnet_mask": "255.255.255.0", "is_wifi": False, "is_link_local": address.startswith("169.254.")})
        except Exception:
            pass
    # Wi-Fi first, then link-local, then the rest.
    interfaces.sort(key=lambda item: (not bool(item.get("is_wifi")), not bool(item.get("is_link_local")), str(item.get("name") or "")))
    return interfaces


def _scan_hosts_for_interface(interface: dict[str, object], *, max_hosts_per_interface: int = 254) -> list[str]:
    address = str(interface.get("ipv4_address") or "").strip()
    mask = str(interface.get("subnet_mask") or "").strip() or "255.255.255.0"
    if not address:
        return []
    hosts: list[str] = []
    try:
        ip = ipaddress.IPv4Address(address)
        if address.startswith("169.254."):
            octets = address.split(".")
            network = ipaddress.IPv4Network(f"169.254.{octets[2]}.0/24", strict=False)
        else:
            network = ipaddress.IPv4Network(f"{address}/{mask}", strict=False)
        for host in network.hosts():
            if str(host) == address:
                continue
            hosts.append(str(host))
            if len(hosts) >= int(max_hosts_per_interface):
                break
    except Exception:
        parts = address.split(".")
        if len(parts) == 4:
            prefix = ".".join(parts[:3])
            hosts = [f"{prefix}.{i}" for i in range(1, 255) if f"{prefix}.{i}" != address]
    return hosts


def _probe_tcp(host: str, port: int, timeout: float) -> tuple[bool, str]:
    try:
        with socket.create_connection((host, int(port)), timeout=float(timeout)):
            return True, ""
    except OSError as exc:
        return False, str(exc)


def scan_red_cameras(
    *,
    port: int = DEFAULT_RCP2_PORT,
    tcp_timeout: float = 0.35,
    identity_timeout: float = 1.5,
    max_workers: int = 48,
    log_callback: Callable[[str], None] | None = None,
) -> list[RedCameraIdentity]:
    """Scan active local networks for RED RCP2 cameras.

    This mirrors the operator expectation from RED Control-style discovery: if
    the user leaves Camera IP blank, MediaRunner probes the active Wi-Fi/local
    subnet(s) for RCP2 WebSocket port 9998 and then asks each responder for
    CAMERA_INFO.
    """
    def log(message: str) -> None:
        if log_callback:
            log_callback(message)

    interfaces = _network_interfaces_for_scan()
    if not interfaces:
        log("No active IPv4 network interfaces found for RED camera scan.")
        return []
    candidate_hosts: list[str] = []
    for interface in interfaces:
        hosts = _scan_hosts_for_interface(interface)
        if hosts:
            label = str(interface.get("label") or interface.get("name") or "network")
            addr = str(interface.get("ipv4_address") or "")
            log(f"Scanning {label} ({addr}) for RCP2 cameras: {len(hosts)} host(s)")
            candidate_hosts.extend(hosts)
    # De-duplicate while preserving order.
    seen: set[str] = set()
    hosts = [h for h in candidate_hosts if not (h in seen or seen.add(h))]
    if not hosts:
        return []
    reachable: list[str] = []
    with ThreadPoolExecutor(max_workers=max(1, int(max_workers))) as executor:
        pending = {executor.submit(_probe_tcp, host, int(port), float(tcp_timeout)): host for host in hosts}
        while pending:
            done, _ = wait(tuple(pending.keys()), timeout=0.1, return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                host = pending.pop(future)
                try:
                    ok, _err = future.result()
                except Exception:
                    ok = False
                if ok:
                    reachable.append(host)
                    log(f"RCP2 port open: {host}:{int(port)}")
    identities: list[RedCameraIdentity] = []
    for host in reachable:
        identity = detect_red_camera_identity(host, port=int(port), timeout=float(identity_timeout))
        if identity.ok:
            identities.append(identity)
            name = identity.camera_name or "RED camera"
            serial = f" serial={identity.serial_number}" if identity.serial_number else ""
            log(f"Detected {name} at {host}{serial}")
        else:
            log(f"RCP2 responder rejected identity read at {host}: {identity.error}")
    return identities



# ── FTPS discovery/download ──────────────────────────────────────────────────

def normalize_reel_identifier(value: str) -> str:
    digits = re.sub(r"\D+", "", str(value or ""))
    if not digits:
        raise ValueError("Reel must contain at least one digit")
    return digits.zfill(3)


def parse_clip_spec(value: str) -> set[int]:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Clip spec is required")
    if text.upper() in {"ALL", "ENTIRE", "ENTIRE_REEL", "*"}:
        return set()
    out: set[int] = set()
    for chunk in [p.strip() for p in text.split(",") if p.strip()]:
        if "-" in chunk:
            a, b = [p.strip() for p in chunk.split("-", 1)]
            start, end = int(a), int(b)
            if end < start:
                raise ValueError(f"Descending clip range: {chunk}")
            out.update(range(start, end + 1))
        else:
            out.add(int(chunk))
    return out


def _connect_ftp(host: str, *, username: str, password: str, port: int, timeout: float, use_ftps: bool):
    if use_ftps:
        if os.name == "nt":
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            ftp = ftplib.FTP_TLS(context=ctx)
        else:
            ftp = ftplib.FTP_TLS()
    else:
        ftp = ftplib.FTP()
    ftp.connect(host, int(port), timeout=float(timeout))
    ftp.login(username, password)
    if use_ftps and hasattr(ftp, "prot_p"):
        ftp.prot_p()
    ftp.set_pasv(True)
    return ftp


def _ftp_cwd_exists(ftp, remote_dir: str) -> bool:
    try:
        current = ftp.pwd()
    except Exception:
        current = "/"
    try:
        ftp.cwd(remote_dir)
        return True
    except Exception:
        return False
    finally:
        try:
            ftp.cwd(current)
        except Exception:
            pass


def _ftp_is_dir(ftp, remote_path: str) -> bool:
    try:
        current = ftp.pwd()
    except Exception:
        current = "/"
    try:
        ftp.cwd(remote_path)
        return True
    except Exception:
        return False
    finally:
        try:
            ftp.cwd(current)
        except Exception:
            pass


def _ftp_list_entries(ftp, remote_dir: str) -> list[tuple[str, str]]:
    try:
        return [(name, facts.get("type", "file")) for name, facts in ftp.mlsd(remote_dir)]
    except Exception:
        pass
    current = ftp.pwd()
    try:
        ftp.cwd(remote_dir)
        names = ftp.nlst()
    finally:
        try:
            ftp.cwd(current)
        except Exception:
            pass
    out: list[tuple[str, str]] = []
    for name in names:
        base = str(name).rstrip("/").split("/")[-1]
        if base in (".", "..", ""):
            continue
        path = name if str(name).startswith("/") else _join_remote(remote_dir, base)
        out.append((base, "dir" if _ftp_is_dir(ftp, path) else "file"))
    return out


def _join_remote(parent: str, child: str) -> str:
    parent = str(parent or "/").rstrip("/")
    if not parent:
        parent = "/"
    return f"/{child}" if parent == "/" else f"{parent}/{child}"


def _remote_size(ftp, remote_path: str) -> int:
    try:
        value = ftp.size(remote_path)
        return int(value or 0)
    except Exception:
        return 0


def _safe_relative(root: str, remote_path: str) -> str:
    root = str(root or "/").rstrip("/")
    path = str(remote_path or "")
    if root and root != "/" and path.startswith(root + "/"):
        return path[len(root) + 1 :].lstrip("/")
    return path.lstrip("/")


def _infer_camera_reel_clip(relative_path: str, fallback_reel: str) -> tuple[str, str, str]:
    parts = [p for p in Path(relative_path).parts if p not in ("/", "")]
    file_name = parts[-1] if parts else relative_path
    upper_path = relative_path.upper()
    camera = ""
    # Common RED-ish naming: G007_A083_... => camera GA, clip 083
    m = re.search(r"([A-Z])0*([0-9]{3})_([A-Z])0*([0-9]{2,3})", upper_path)
    if m:
        camera = f"{m.group(1)}{m.group(3)}"
        reel = m.group(2).zfill(3)
        clip = m.group(4).zfill(3)
        return camera, reel, clip
    # Fallback: first path component often represents camera/volume/card.
    if parts:
        candidate = re.sub(r"[^A-Z0-9]", "", parts[0].upper())
        if 1 <= len(candidate) <= 6:
            camera = candidate
    clip = ""
    c = re.search(r"(?:^|[_/.-])C?0*([0-9]{2,3})(?:[_/.-]|$)", upper_path)
    if c:
        clip = c.group(1).zfill(3)
    return camera, fallback_reel, clip


def _file_matches_selection(remote_path: str, reel: str, clip_numbers: set[int] | None) -> bool:
    if not remote_path.lower().endswith(".r3d"):
        return False
    up = remote_path.upper()
    # RED paths normally include the reel token in the .RDM folder or file name.
    # Requiring it prevents an "entire reel" pull from accidentally matching the whole card.
    if reel and reel not in up:
        return False
    if not clip_numbers:
        return True
    for n in clip_numbers:
        tokens = {f"{n:03d}", f"{n:02d}"}
        # Prefer delimiter-like matches so reel 007 does not match clip 007 accidentally in every path.
        for token in tokens:
            if re.search(rf"(?:^|[^0-9])0*{re.escape(token)}(?:[^0-9]|$)", up):
                return True
    return False


def _discover_files_recursive(
    ftp,
    *,
    remote_root: str,
    reel: str,
    clip_numbers: set[int] | None,
    max_depth: int,
    log: Callable[[str], None] | None = None,
) -> list[RedWirelessMediaFile]:
    files: list[RedWirelessMediaFile] = []
    visited: set[str] = set()

    def walk(remote_dir: str, depth: int) -> None:
        if depth > max_depth:
            return
        remote_dir = remote_dir or "/"
        if remote_dir in visited:
            return
        visited.add(remote_dir)
        try:
            entries = _ftp_list_entries(ftp, remote_dir)
        except Exception as exc:
            if log:
                log(f"List failed: {remote_dir} ({exc})")
            return
        for name, typ in entries:
            if name in (".", "..", ""):
                continue
            path = _join_remote(remote_dir, name)
            is_dir = str(typ).lower() == "dir" or _ftp_is_dir(ftp, path)
            if is_dir:
                walk(path, depth + 1)
                continue
            if not _file_matches_selection(path, reel, clip_numbers):
                continue
            rel = _safe_relative(remote_root, path)
            camera, inferred_reel, clip = _infer_camera_reel_clip(rel, reel)
            files.append(
                RedWirelessMediaFile(
                    remote_path=path,
                    relative_path=rel,
                    size_bytes=_remote_size(ftp, path),
                    camera=camera,
                    reel=inferred_reel or reel,
                    clip=clip,
                    file_name=Path(path).name,
                )
            )

    walk(remote_root, 0)
    # De-duplicate by remote path while preserving sort.
    dedup: dict[str, RedWirelessMediaFile] = {}
    for f in files:
        dedup[f.remote_path] = f
    return sorted(dedup.values(), key=lambda item: item.remote_path)


def discover_red_wireless_media(
    *,
    host: str,
    reel: str,
    clip_spec: str,
    username: str = DEFAULT_FTP_USERNAME,
    password: str = DEFAULT_FTP_PASSWORD,
    port: int = DEFAULT_FTPS_PORT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    use_ftps: bool = True,
    remote_root: str = "auto",
    max_depth: int = 6,
    log_callback: Callable[[str], None] | None = None,
) -> RedWirelessDiscovery:
    log_lines: list[str] = []
    def log(msg: str) -> None:
        log_lines.append(msg)
        if log_callback:
            log_callback(msg)

    protocol = "FTPS" if use_ftps else "FTP"
    host = str(host or "").strip()
    try:
        reel_id = normalize_reel_identifier(reel)
        parsed_clips = parse_clip_spec(clip_spec)
        clips = parsed_clips or None
        log(f"Connecting {protocol} {host}:{int(port)}")
        ftp = _connect_ftp(host, username=username, password=password, port=int(port), timeout=float(timeout), use_ftps=use_ftps)
        try:
            roots = list(DEFAULT_REMOTE_ROOTS) if str(remote_root or "auto").strip().lower() in {"", "auto"} else [str(remote_root).strip()]
            best_root = ""
            best_files: list[RedWirelessMediaFile] = []
            for root in roots:
                if not _ftp_cwd_exists(ftp, root):
                    log(f"Root unavailable: {root}")
                    continue
                log(f"Scanning remote root: {root}")
                files = _discover_files_recursive(ftp, remote_root=root, reel=reel_id, clip_numbers=clips, max_depth=max_depth, log=log)
                log(f"{root}: {len(files)} matched R3D file(s)")
                if len(files) > len(best_files):
                    best_root = root
                    best_files = files
            total = sum(int(f.size_bytes or 0) for f in best_files)
            if not best_files:
                return RedWirelessDiscovery(False, host, protocol, remote_root=best_root or "", reel=reel_id, clip_spec=clip_spec, files=[], total_bytes=0, error="No matching R3D files found. Confirm RED Media Access is enabled and reel/clip selection is correct.", log_lines=log_lines)
            return RedWirelessDiscovery(True, host, protocol, remote_root=best_root, reel=reel_id, clip_spec=clip_spec, files=best_files, total_bytes=total, log_lines=log_lines)
        finally:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass
    except Exception as exc:
        return RedWirelessDiscovery(False, host, protocol, reel=str(reel or ""), clip_spec=str(clip_spec or ""), files=[], total_bytes=0, error=str(exc), log_lines=log_lines)


# ── Streaming copy / verification ────────────────────────────────────────────

def _new_hash():
    try:
        import xxhash  # type: ignore
        return "xxh128", xxhash.xxh128()
    except Exception:
        return "sha256", hashlib.sha256()


def _final_hash(name: str, h) -> str:
    value = h.hexdigest()
    return value if name == "xxh128" else f"sha256:{value}"


def hash_local_file(path: Path, *, chunk_size: int = 8 * 1024 * 1024) -> str:
    name, h = _new_hash()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            h.update(chunk)
    return _final_hash(name, h)


def hash_remote_file(ftp, remote_path: str) -> str:
    name, h = _new_hash()
    ftp.retrbinary(f"RETR {remote_path}", lambda data: h.update(data))
    return _final_hash(name, h)


def download_remote_file(
    ftp,
    media_file: RedWirelessMediaFile,
    dst: Path,
    *,
    progress_callback: Callable[[int], None] | None = None,
    chunk_size: int = 8 * 1024 * 1024,
) -> None:
    """Download with REST resume support (audit fix #2): an interrupted .part
    continues from its last byte instead of restarting a multi-GB Wi-Fi pull."""
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_name(dst.name + ".part")
    expected = int(media_file.size_bytes or 0)

    resume_offset = 0
    if part.exists():
        existing = part.stat().st_size
        if expected > 0 and 0 < existing < expected:
            resume_offset = existing
        else:
            part.unlink()

    def stream(rest: int) -> None:
        mode = "ab" if rest else "wb"
        with part.open(mode) as writer:
            def cb(data: bytes) -> None:
                writer.write(data)
                if progress_callback:
                    progress_callback(len(data))
            ftp.retrbinary(f"RETR {media_file.remote_path}", cb, blocksize=int(chunk_size), rest=rest or None)
            writer.flush()
            os.fsync(writer.fileno())

    if resume_offset:
        try:
            logger.info("Resuming %s at byte %d of %d", media_file.remote_path, resume_offset, expected)
            stream(resume_offset)
        except (ftplib.error_perm, ftplib.error_temp) as exc:
            logger.warning("REST resume rejected for %s (%s); restarting from 0", media_file.remote_path, exc)
            if part.exists():
                part.unlink()
            stream(0)
    else:
        stream(0)
    actual = part.stat().st_size
    if expected and actual != expected:
        # Keep the .part so a retry can resume it (audit fixes #1/#2).
        raise IOError(f"Size mismatch after download: expected {expected}, got {actual}")
    part.replace(dst)


def _safe_token(value: str, fallback: str = "run") -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or fallback).strip()).strip("._-")
    return cleaned or fallback


def run_red_wireless_ingest(
    *,
    host: str,
    discovery: RedWirelessDiscovery,
    destinations: list[tuple[str, Path, int]],
    username: str = DEFAULT_FTP_USERNAME,
    password: str = DEFAULT_FTP_PASSWORD,
    port: int = DEFAULT_FTPS_PORT,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    use_ftps: bool = True,
    verify: bool = True,
    second_pass: bool = True,
    progress_callback: Callable[[int, int, str], None] | None = None,
    rate_callback: Callable[[float, str], None] | None = None,
    row_callback: Callable[[dict], None] | None = None,
    dest_progress_callback: Callable[[dict], None] | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> RedWirelessIngestResult:
    if not discovery.ok or not discovery.files:
        raise ValueError(discovery.error or "Discovery did not produce any media files")
    if not destinations:
        raise ValueError("At least one destination is required")

    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    files = list(discovery.files or [])
    total_bytes_camera = sum(int(f.size_bytes or 0) for f in files)
    primary_role, primary_root, _primary_row = destinations[0]
    primary_root = Path(primary_root).expanduser()
    primary_root.mkdir(parents=True, exist_ok=True)
    protocol = "FTPS" if use_ftps else "FTP"
    ts = time.strftime("%Y%m%d_%H%M%S")
    manifest_paths: list[str] = []
    report_paths: list[str] = []
    ok_count = 0
    fail_count = 0

    manifests: dict[str, Manifest] = {}
    for role, root, _row in destinations:
        root = Path(root).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        manifest_path = root / "_manifests" / f"MediaRunner_RED_Wireless_{_safe_token(role, 'Destination')}_{ts}.csv"
        manifests[role] = Manifest(manifest_path)
        manifest_paths.append(str(manifest_path))

    copied_primary: list[tuple[RedWirelessMediaFile, Path]] = []
    bytes_written = 0
    copy_started = time.perf_counter()
    total_work_files = len(files) * max(1, len(destinations))
    progress_file_done = 0

    # Connection holder + reconnect hooks (audit fixes #1/#3): one Wi-Fi drop
    # must not fail every remaining file in the ingest.
    conn: dict[str, object] = {"ftp": None}

    def _connect(_attempt_index: int = 0):
        conn["ftp"] = _connect_ftp(str(host), username=username, password=password,
                                   port=int(port), timeout=float(timeout), use_ftps=use_ftps)
        return conn["ftp"]

    def _close_conn() -> None:
        ftp_obj = conn.get("ftp")
        if ftp_obj is None:
            return
        try:
            ftp_obj.quit()
        except Exception:
            try:
                ftp_obj.close()
            except Exception:
                pass
        conn["ftp"] = None

    def _reconnect(attempt: int, exc: BaseException) -> None:
        log(f"Reconnecting to {host} after error (attempt {attempt}): {exc}")
        _close_conn()
        _connect()

    def _keepalive() -> None:
        ftp_obj = conn.get("ftp")
        if ftp_obj is None:
            return
        try:
            ftp_obj.voidcmd("NOOP")
        except Exception:
            logger.debug("NOOP keepalive failed for %s", host)

    def _remote_hash_with_retry(remote_path: str) -> str:
        value, _retries = retry_operation(
            lambda _a: hash_remote_file(conn["ftp"], remote_path),
            on_retry=_reconnect,
            description=f"hash {remote_path}",
        )
        return value

    retry_operation(_connect, description=f"connect {host}")
    try:
        log(f"RED Wireless Ingest: {protocol} {host}:{int(port)} → {primary_root}")
        log(f"Matched files: {len(files)} / {human_size(total_bytes_camera)}")
        if second_pass and verify:
            log("Checksum mode: second pass")
        elif verify:
            log("Checksum mode: inline after each file")
        else:
            log("Checksum mode: OFF")

        for idx, media_file in enumerate(files, start=1):
            dst = primary_root / media_file.relative_path
            status = TransferStatus.DOWNLOADED
            note = ""
            src_hash = ""
            dst_hash = ""
            try:
                overwrite_note = ""
                if dst.exists() and verify:
                    existing_remote_hash = _remote_hash_with_retry(media_file.remote_path)
                    existing_local_hash = hash_local_file(dst)
                    if (
                        int(media_file.size_bytes or 0) > 0
                        and dst.stat().st_size == int(media_file.size_bytes or 0)
                        and existing_remote_hash == existing_local_hash
                    ):
                        status = TransferStatus.SKIPPED_EXISTING_VERIFIED
                        note = "Existing destination verified against camera"
                        src_hash = existing_remote_hash
                        dst_hash = existing_local_hash
                        local_result = verify_local_artifact(
                            dst,
                            expected_size=int(media_file.size_bytes or 0),
                            matched_status=TransferStatus.SKIPPED_EXISTING_VERIFIED,
                            note=note,
                        )
                        manifests[primary_role].write(**verification_result_to_manifest_kwargs(
                            local_result,
                            method="RED Wireless Ingest",
                            source_path=f"{protocol.lower()}://{host}{media_file.remote_path}",
                            destination_path=str(dst),
                            camera=media_file.camera,
                            reel=media_file.reel,
                            clip=media_file.clip,
                            file=media_file.file_name,
                            src_hash=src_hash,
                            dst_hash=dst_hash,
                        ))
                        if row_callback:
                            row_callback({"status": status, "destination": primary_role, "camera": media_file.camera, "ip": host, "reel": media_file.reel, "clip": media_file.clip, "file": media_file.file_name, "note": note})
                        copied_primary.append((media_file, dst))
                        ok_count += 1
                        log(f"Primary skipped verified: {media_file.relative_path}")
                        continue
                    overwrite_note = "Existing destination failed verification and was replaced"
                elif dst.exists():
                    overwrite_note = "Existing destination was not trusted without verification and was replaced"

                if not dst.exists() or overwrite_note:
                    log(f"Primary download [{idx}/{len(files)}]: {media_file.relative_path}")
                    def progress_bytes(n: int) -> None:
                        nonlocal bytes_written
                        bytes_written += int(n)
                        elapsed = max(0.001, time.perf_counter() - copy_started)
                        if progress_callback:
                            progress_callback(progress_file_done, total_work_files, media_file.file_name)
                        if rate_callback:
                            rate_callback(bytes_written / elapsed, "RED Wireless")
                    _, _dl_retries = retry_operation(
                        lambda _a: download_remote_file(conn["ftp"], media_file, dst, progress_callback=progress_bytes),
                        on_retry=_reconnect,
                        description=f"download {media_file.relative_path}",
                    )
                    if _dl_retries:
                        log(f"Recovered after {_dl_retries} retr{'y' if _dl_retries == 1 else 'ies'}: {media_file.relative_path}")
                local_result = verify_local_artifact(
                    dst,
                    expected_size=int(media_file.size_bytes or 0),
                    matched_status=TransferStatus.DOWNLOADED,
                    note=overwrite_note or "Downloaded and locally checksummed",
                )
                dst_hash = local_result.destination_checksums.get("xxh128", "")
                if verify and not second_pass:
                    src_hash = _remote_hash_with_retry(media_file.remote_path)
                    if src_hash != dst_hash:
                        status = TransferStatus.MISMATCH
                        note = "Hash mismatch after camera download"
                    else:
                        status = TransferStatus.VERIFIED
                        note = overwrite_note or "Camera → Primary verified"
                else:
                    status = local_result.status if not verify else TransferStatus.DOWNLOADED
                    note = overwrite_note or local_result.note
                manifests[primary_role].write(**verification_result_to_manifest_kwargs(
                    local_result,
                    method="RED Wireless Ingest",
                    source_path=f"{protocol.lower()}://{host}{media_file.remote_path}",
                    destination_path=str(dst),
                    camera=media_file.camera,
                    reel=media_file.reel,
                    clip=media_file.clip,
                    file=media_file.file_name,
                    src_hash=src_hash,
                    dst_hash=dst_hash,
                    status=status,
                    verification_status=status,
                    note=note,
                ))
                row = {
                    "status": status,
                    "destination": primary_role,
                    "camera": media_file.camera,
                    "ip": host,
                    "reel": media_file.reel,
                    "clip": media_file.clip,
                    "file": media_file.file_name,
                    "note": note,
                }
                if row_callback:
                    row_callback(row)
                copied_primary.append((media_file, dst))
                if status == TransferStatus.MISMATCH:
                    fail_count += 1
                else:
                    ok_count += 1
            except Exception as exc:
                fail_count += 1
                note = str(exc)
                manifests[primary_role].write(
                    **verification_result_to_manifest_kwargs(
                        verify_local_artifact(dst, expected_size=int(media_file.size_bytes or 0), matched_status=TransferStatus.FAILED, note=note) if dst.exists() else VerificationResult(status=TransferStatus.FAILED, destination_path=str(dst), source_size=int(media_file.size_bytes or 0), error=note, note=note, verification_time=time.strftime("%Y-%m-%d %H:%M:%S")),
                        method="RED Wireless Ingest",
                        source_path=f"{protocol.lower()}://{host}{media_file.remote_path}",
                        destination_path=str(dst),
                        camera=media_file.camera,
                        reel=media_file.reel,
                        clip=media_file.clip,
                        file=media_file.file_name,
                        status=TransferStatus.FAILED,
                        verification_status=TransferStatus.FAILED,
                        error=note,
                        note=note,
                    )
                )
                if row_callback:
                    row_callback({"status": TransferStatus.FAILED, "destination": primary_role, "camera": media_file.camera, "ip": host, "reel": media_file.reel, "clip": media_file.clip, "file": media_file.file_name, "note": note})
                log(f"Primary ERROR: {media_file.relative_path}: {exc}")
            finally:
                progress_file_done += 1
                if progress_callback:
                    progress_callback(progress_file_done, total_work_files, media_file.file_name)
                if dest_progress_callback:
                    dest_progress_callback({"role": primary_role, "done": progress_file_done, "total": len(files), "status": "Running"})
                _keepalive()

        if verify and second_pass:
            log("Second pass checksum: camera → primary")
            for media_file, dst in copied_primary:
                try:
                    src_hash = _remote_hash_with_retry(media_file.remote_path)
                    dst_hash = hash_local_file(dst)
                    ok = src_hash == dst_hash
                    local_result = verify_local_artifact(
                        dst,
                        expected_size=int(media_file.size_bytes or 0),
                        matched_status=TransferStatus.VERIFIED if ok else TransferStatus.MISMATCH,
                        note="Camera → Primary" if ok else "Camera → Primary hash mismatch",
                    )
                    manifests[primary_role].write(**verification_result_to_manifest_kwargs(
                        local_result,
                        method="RED Wireless Verify",
                        source_path=f"{protocol.lower()}://{host}{media_file.remote_path}",
                        destination_path=str(dst),
                        camera=media_file.camera,
                        reel=media_file.reel,
                        clip=media_file.clip,
                        file=media_file.file_name,
                        src_hash=src_hash,
                        dst_hash=dst_hash,
                        status=TransferStatus.VERIFIED if ok else TransferStatus.MISMATCH,
                        verification_status=TransferStatus.VERIFIED if ok else TransferStatus.MISMATCH,
                    ))
                    if row_callback:
                        row_callback({"status": TransferStatus.VERIFIED if ok else TransferStatus.MISMATCH, "destination": primary_role, "camera": media_file.camera, "ip": host, "reel": media_file.reel, "clip": media_file.clip, "file": media_file.file_name, "note": "Camera → Primary"})
                    if not ok:
                        fail_count += 1
                except Exception as exc:
                    fail_count += 1
                    manifests[primary_role].write(method="RED Wireless Verify", source_path=f"{protocol.lower()}://{host}{media_file.remote_path}", destination_path=str(dst), camera=media_file.camera, reel=media_file.reel, clip=media_file.clip, file=media_file.file_name, status=TransferStatus.FAILED, verification_status=TransferStatus.FAILED, error=str(exc), note=str(exc))
                    log(f"Verify ERROR: {media_file.relative_path}: {exc}")

    finally:
        _close_conn()

    # Fan-out from Primary to additional destinations. This avoids repeated Wi-Fi reads.
    for dest_index, (role, root, _row) in enumerate(destinations[1:], start=2):
        root = Path(root).expanduser()
        log(f"Fan-out: Primary → {role} ({root})")
        done = 0
        for media_file, primary_path in copied_primary:
            dst = root / media_file.relative_path
            src_hash = ""
            dst_hash = ""
            status = TransferStatus.COPIED
            note = "Primary → Destination"
            try:
                if not primary_path.exists():
                    raise FileNotFoundError(primary_path)
                overwrite_note = ""
                if dst.exists():
                    existing = assess_existing_destination(primary_path, dst)
                    if existing.status == TransferStatus.SKIPPED_EXISTING_VERIFIED:
                        status = existing.status
                        note = existing.note
                        src_hash = existing.source_checksums.get("xxh128", "")
                        dst_hash = existing.destination_checksums.get("xxh128", "")
                        manifests[role].write(**verification_result_to_manifest_kwargs(
                            existing,
                            method="RED Wireless Fanout",
                            source_path=str(primary_path),
                            destination_path=str(dst),
                            camera=media_file.camera,
                            reel=media_file.reel,
                            clip=media_file.clip,
                            file=media_file.file_name,
                        ))
                        if row_callback:
                            row_callback({"status": status, "destination": role, "camera": media_file.camera, "ip": host, "reel": media_file.reel, "clip": media_file.clip, "file": media_file.file_name, "note": note})
                        ok_count += 1
                        done += 1
                        progress_file_done += 1
                        if progress_callback:
                            progress_callback(min(progress_file_done, total_work_files), total_work_files, media_file.file_name)
                        if dest_progress_callback:
                            dest_progress_callback({"role": role, "done": done, "total": len(copied_primary), "status": "Running"})
                        continue
                    overwrite_note = "Existing destination failed verification and was replaced"
                if not dst.exists() or overwrite_note:
                    copy2_with_progress(primary_path, dst, progress_callback=lambda n: None)
                overwrite_note = overwrite_note or note
                if verify:
                    compare = verify_file_pair(primary_path, dst)
                    src_hash = compare.source_checksums.get("xxh128", "")
                    dst_hash = compare.destination_checksums.get("xxh128", "")
                    if compare.status == TransferStatus.VERIFIED:
                        status = TransferStatus.VERIFIED
                        note = overwrite_note or "Primary → Destination verified"
                    else:
                        status = TransferStatus.MISMATCH
                        note = compare.note
                    manifests[role].write(**verification_result_to_manifest_kwargs(
                        compare,
                        method="RED Wireless Fanout",
                        source_path=str(primary_path),
                        destination_path=str(dst),
                        camera=media_file.camera,
                        reel=media_file.reel,
                        clip=media_file.clip,
                        file=media_file.file_name,
                        status=status,
                        verification_status=status,
                        note=note,
                    ))
                else:
                    local_result = verify_local_artifact(
                        dst,
                        expected_size=primary_path.stat().st_size,
                        matched_status=TransferStatus.COPIED,
                        note=overwrite_note or note,
                    )
                    dst_hash = local_result.destination_checksums.get("xxh128", "")
                    manifests[role].write(**verification_result_to_manifest_kwargs(
                        local_result,
                        method="RED Wireless Fanout",
                        source_path=str(primary_path),
                        destination_path=str(dst),
                        camera=media_file.camera,
                        reel=media_file.reel,
                        clip=media_file.clip,
                        file=media_file.file_name,
                        status=TransferStatus.COPIED,
                        verification_status=TransferStatus.COPIED,
                    ))
                if row_callback:
                    row_callback({"status": status, "destination": role, "camera": media_file.camera, "ip": host, "reel": media_file.reel, "clip": media_file.clip, "file": media_file.file_name, "note": note})
                if status == TransferStatus.MISMATCH:
                    fail_count += 1
                else:
                    ok_count += 1
            except Exception as exc:
                fail_count += 1
                manifests[role].write(method="RED Wireless Fanout", source_path=str(primary_path), destination_path=str(dst), camera=media_file.camera, reel=media_file.reel, clip=media_file.clip, file=media_file.file_name, status=TransferStatus.FAILED, verification_status=TransferStatus.FAILED, error=str(exc), note=str(exc))
                if row_callback:
                    row_callback({"status": TransferStatus.FAILED, "destination": role, "camera": media_file.camera, "ip": host, "reel": media_file.reel, "clip": media_file.clip, "file": media_file.file_name, "note": str(exc)})
            done += 1
            progress_file_done += 1
            if progress_callback:
                progress_callback(min(progress_file_done, total_work_files), total_work_files, media_file.file_name)
            if dest_progress_callback:
                dest_progress_callback({"role": role, "done": done, "total": len(copied_primary), "status": "Running"})

    for role, root, _row in destinations:
        manifest_path = manifests[role].path
        report_path = manifest_path.parent / f"MediaRunner_RED_Wireless_{_safe_token(role, 'Destination')}_{ts}.html"
        try:
            write_html_report(manifest_path, f"RED_Wireless_{discovery.reel}", report_path, source_path=f"{protocol}://{host}{discovery.remote_root}", destination_path=str(root), method_label="RED Wireless Ingest")
            report_paths.append(str(report_path))
        except Exception as exc:
            log(f"Report failed for {role}: {exc}")

    return RedWirelessIngestResult(ok=(fail_count == 0), ok_count=ok_count, fail_count=fail_count, manifest_paths=manifest_paths, report_paths=report_paths)
