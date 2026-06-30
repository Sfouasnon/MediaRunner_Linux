#!/usr/bin/env python3
"""
MediaRunner metadata extraction helpers.

Supports:
- RED / R3D via REDline printMeta modes 0–6, condensed into report-safe scalar fields
- Generic MOV / MP4 / MXF / other common media via FFmpeg's ffprobe
- Optional ExifTool enrichment / raw JSON sidecars

The legacy entry points find_redline(), run_redline(), summarize(), and run_meta()
remain available for older callers.
"""
from __future__ import annotations

import csv
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).parent))
from mediarunner_core import Manifest, parse_camera_label

GENERIC_MEDIA_EXTENSIONS = {
    ".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".webm", ".braw", ".crm", ".wav"
}
RED_EXTENSIONS = {".r3d"}

CSV_FIELDS = [
    "status", "camera_family", "camera", "file", "source_file", "metadata_type", "tool",
    "ltc_in", "ltc_out", "start_tc", "end_tc", "duration", "fps", "frame_count",
    "resolution", "codec", "camera_model", "serial_number", "reel", "clip_id",
    "creation_date", "warnings",
]

ProgressCallback = Callable[[int, int, Path, dict], None]
LogCallback = Callable[[str], None]


@dataclass(frozen=True)
class ToolProbe:
    key: str
    label: str
    path: str
    ok: bool
    message: str


@dataclass(frozen=True)
class MetadataRunResult:
    master_csv: Path
    report_html: Path
    rows: list[dict]
    raw_dir: Optional[Path]


def _is_executable(path: str | Path) -> bool:
    p = Path(path).expanduser()
    return p.exists() and p.is_file() and os.access(str(p), os.X_OK)


def _candidate_dirs() -> list[Path]:
    dirs = [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), Path("/usr/bin"), Path("/bin")]
    return dirs


def _redline_bundle_candidate(path: Path) -> Optional[Path]:
    bundle = None
    for current in [path, *path.parents]:
        if current.suffix.lower() == ".app":
            bundle = current
            break
    if not bundle:
        return None
    macos = bundle / "Contents" / "MacOS"
    for name in ("REDline", "REDLine", "redline"):
        candidate = macos / name
        if _is_executable(candidate):
            return candidate.resolve()
    if macos.is_dir():
        for candidate in sorted(macos.iterdir(), key=lambda p: p.name.lower()):
            if candidate.is_file() and "redline" in candidate.name.lower() and _is_executable(candidate):
                return candidate.resolve()
    return None


def resolve_tool(tool_name: str, override: str = "") -> Optional[str]:
    """Resolve a CLI tool with explicit path, PATH, and common macOS prefixes."""
    override = (override or "").strip()
    if override:
        candidate = Path(override).expanduser()
        if tool_name.lower() == "redline":
            bundle_candidate = _redline_bundle_candidate(candidate)
            if bundle_candidate:
                return str(bundle_candidate)
        if _is_executable(candidate):
            return str(candidate.resolve())
    if tool_name.lower() == "redline":
        for app in (
            Path("/Applications/REDCINE-X Professional/REDCINE-X PRO.app"),
            Path("/Applications/REDCINE-X PRO.app"),
            Path("/Applications/REDCINE-X PRO 64-bit.app"),
        ):
            bundle_candidate = _redline_bundle_candidate(app)
            if bundle_candidate:
                return str(bundle_candidate)
    found = shutil.which(tool_name)
    if found and _is_executable(found):
        return str(Path(found).resolve())
    variants = [tool_name]
    if tool_name.lower() == "redline":
        variants = ["REDline", "REDLine", "redline"]
    for directory in _candidate_dirs():
        for name in variants:
            candidate = directory / name
            if _is_executable(candidate):
                return str(candidate.resolve())
    return None


def resolve_tools_from_config(cfg: Optional[dict] = None) -> dict[str, Optional[str]]:
    cfg = cfg or {}
    return {
        "redline": resolve_tool("REDline", str(cfg.get("redline_path", "") or "")),
        "ffmpeg": resolve_tool("ffmpeg", str(cfg.get("ffmpeg_path", "") or "")),
        "ffprobe": resolve_tool("ffprobe", str(cfg.get("ffprobe_path", "") or "")),
        "exiftool": resolve_tool("exiftool", str(cfg.get("exiftool_path", "") or "")),
    }


def _tool_version(path: str, args: list[str], timeout: float = 5.0) -> str:
    try:
        proc = subprocess.run([path, *args], capture_output=True, text=True, timeout=timeout, check=False)
        text = (proc.stdout or proc.stderr or "").strip().splitlines()
        return text[0][:220] if text else "Found"
    except Exception as exc:
        return f"Found, but version check failed: {exc}"


def probe_metadata_tools(cfg: Optional[dict] = None) -> dict[str, ToolProbe]:
    tools = resolve_tools_from_config(cfg)
    probes: dict[str, ToolProbe] = {}
    labels = {"redline": "REDline", "ffmpeg": "FFmpeg", "ffprobe": "FFprobe", "exiftool": "ExifTool"}
    version_args = {"redline": ["--help"], "ffmpeg": ["-version"], "ffprobe": ["-version"], "exiftool": ["-ver"]}
    for key, label in labels.items():
        path = tools.get(key) or ""
        if not path:
            probes[key] = ToolProbe(key, label, "", False, "Not found")
        else:
            version = _tool_version(path, version_args[key])
            probes[key] = ToolProbe(key, label, path, True, version)
    return probes


# ── REDline legacy-compatible helpers ────────────────────────────────────────

def find_redline(override: str = "") -> str:
    path = resolve_tool("REDline", override)
    if path:
        return path
    raise RuntimeError("REDline not found. Add it to PATH, install REDCINE-X PRO, or choose it in Settings → Metadata Tools.")


def run_redline(redline: str, r3d: Path, out_csv: Path, mode: str = "5", timeout: float = 300.0):
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    cmd = [redline, "--i", str(r3d), "--printMeta", str(mode), "--useMeta"]
    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        # timeout (audit fix #13): a hung REDline must not hang the metadata
        # worker forever.
        proc = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if proc.returncode != 0:
        if out_csv.exists() and out_csv.stat().st_size > 0:
            return  # REDline sometimes returns non-zero despite usable output.
        raise RuntimeError(proc.stderr.strip() or f"REDline failed on {r3d.name}")


def _run_redline_capture(redline: str, r3d: Path, mode: str, timeout: float = 60.0) -> str:
    cmd = [redline, "--i", str(r3d), "--printMeta", str(mode), "--useMeta"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    payload = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    if proc.returncode != 0 and not payload:
        raise RuntimeError(proc.stderr.strip() or f"REDline printMeta {mode} failed on {r3d.name}")
    return payload


def normalize_tc(tc) -> str:
    text = str(tc or "").strip()
    if not text:
        return ""
    # RED sometimes emits HH:MM:SS.FF.
    if re.match(r"^\d{2}:\d{2}:\d{2}\.\d{2}$", text):
        text = text[:8] + ":" + text[9:]
    return text


def _parse_rate(value: object) -> str:
    text = str(value or "").strip().lower().replace("fps", "").strip()
    if not text:
        return ""
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            rate = float(left) / float(right)
            return _format_rate(rate)
        except Exception:
            return ""
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return ""
    try:
        return _format_rate(float(match.group(0)))
    except Exception:
        return ""


def _format_rate(rate: float) -> str:
    common = [23.976, 24, 25, 29.97, 30, 47.952, 48, 50, 59.94, 60, 90, 96, 100, 119.88, 120]
    nearest = min(common, key=lambda x: abs(x - rate))
    rate = nearest if abs(nearest - rate) < 0.02 else rate
    return str(int(round(rate))) if abs(rate - round(rate)) < 0.001 else f"{rate:.3f}".rstrip("0").rstrip(".")


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("[")]
    if not lines:
        return []
    header_index = 0
    for i, line in enumerate(lines):
        cells = [c.strip().lower().replace(" ", "") for c in next(csv.reader([line]))]
        if any("timecode" in c for c in cells) or "tc" in cells:
            header_index = i
            break
    reader = csv.DictReader(lines[header_index:])
    return [{str(k).strip(): (v.strip() if isinstance(v, str) else "") for k, v in row.items() if k} for row in reader if row]


def estimate_fps_from_rows(rows: list[dict[str, str]]) -> str:
    ts_key = next((k for k in rows[0].keys() if k.lower().replace(" ", "") in {"timestamp", "timecodeusec", "timeus"}), "") if rows else ""
    if not ts_key or len(rows) < 2:
        return ""
    values: list[float] = []
    for row in rows:
        try:
            values.append(float(str(row.get(ts_key, "")).strip()))
        except Exception:
            pass
    if len(values) < 2:
        return ""
    deltas = sorted(b - a for a, b in zip(values, values[1:]) if b > a)
    if not deltas:
        return ""
    median = deltas[len(deltas)//2]
    if median <= 0:
        return ""
    # RED per-frame timestamps are commonly in microseconds.
    return _format_rate(1_000_000.0 / median)


def summarize(per_frame_csv: Path, r3d: Path) -> dict:
    rows = _read_csv_rows(per_frame_csv)
    if not rows:
        raise RuntimeError(f"Empty CSV: {per_frame_csv.name}")
    first = rows[0]
    tc_col = next((c for c in first.keys() if c.lower().replace(" ", "") in {"timecode", "tc", "abstimecode", "edgetimecode"}), None)
    if not tc_col:
        tc_col = next((c for c in first.keys() if "timecode" in c.lower()), None)
    if not tc_col:
        raise RuntimeError(f"No timecode column in {per_frame_csv.name}. Cols: {list(first.keys())}")
    fps = estimate_fps_from_rows(rows)
    return {
        "camera": parse_camera_label(r3d.name),
        "file": r3d.name,
        "ltc_in": normalize_tc(rows[0].get(tc_col, "")),
        "ltc_out": normalize_tc(rows[-1].get(tc_col, "")),
        "fps": fps,
        "frame_count": str(len(rows)),
    }


def _looks_like_redline_key_dump(value: object) -> bool:
    """Detect REDline/RCP header/key lists that are not actual scalar values."""
    text = str(value or "").strip()
    if not text:
        return False
    comma_count = text.count(",")
    if len(text) > 180 and comma_count >= 6:
        return True
    key_terms = (
        "luma curve", "red curve", "shutter", "aperture", "focus distance",
        "lens", "camera notes", "frame guide", "aspect ratio", "production name",
        "operator", "director", "copyright", "motion mount", "genlock",
    )
    hits = sum(1 for term in key_terms if term in text.lower())
    return comma_count >= 4 and hits >= 2


def _is_plausible_redline_header(row: list[str]) -> bool:
    cells = [str(c or "").strip() for c in row if str(c or "").strip()]
    if len(cells) < 2:
        return False
    alpha = sum(1 for c in cells if re.search(r"[A-Za-z]", c))
    # REDline CSV headers are usually human-readable labels, not pure numbers/timecodes.
    return alpha >= max(2, int(len(cells) * 0.6))


def _is_plausible_redline_value_row(row: list[str]) -> bool:
    cells = [str(c or "").strip() for c in row]
    joined = ",".join(cells)
    if not joined.strip():
        return False
    if _looks_like_redline_key_dump(joined):
        return False
    return True


def parse_redline_fields(text: str) -> dict[str, str]:
    """Parse REDline --printMeta text/CSV into a condensed key/value dict.

    REDline's printMeta modes vary: some emit key/value text, some emit CSV,
    and mode 3 can include both a header and CSV block. This parser extracts
    scalar values conservatively and refuses to promote metadata header dumps as
    values.
    """
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip() and not line.lstrip().startswith("[")]
    fields: dict[str, str] = {}

    # Key/value text modes: "Camera Model: ..." or "Camera Model = ...".
    for line in lines:
        clean = re.sub(r"^\[[^\]]+\]\s*", "", line).strip()
        # Skip obvious CSV rows here; they are handled below.
        if clean.count(",") >= 2 and not (":" in clean[:80] or "=" in clean[:80]):
            continue
        sep = ":" if ":" in clean else "=" if "=" in clean else ""
        if not sep:
            continue
        key, value = clean.split(sep, 1)
        key = key.strip().strip('"')
        value = value.strip().strip('"')
        if key and value and not _looks_like_redline_key_dump(value):
            fields[key] = value

    # Two-column and header/value CSV modes.
    parsed_rows: list[list[str]] = []
    for line in lines:
        if "," not in line:
            continue
        try:
            row = [str(c or "").strip().strip('"') for c in next(csv.reader([line]))]
        except Exception:
            continue
        if any(row):
            parsed_rows.append(row)

    for row in parsed_rows:
        if len(row) == 2 and row[0] and row[1] and not _looks_like_redline_key_dump(row[1]):
            fields.setdefault(row[0], row[1])

    for i in range(len(parsed_rows) - 1):
        header = parsed_rows[i]
        values = parsed_rows[i + 1]
        if len(header) != len(values) or len(header) < 2:
            continue
        if not _is_plausible_redline_header(header) or not _is_plausible_redline_value_row(values):
            continue
        for h, v in zip(header, values):
            h = str(h or "").strip().strip('"')
            v = str(v or "").strip().strip('"')
            if h and v and not _looks_like_redline_key_dump(v):
                fields.setdefault(h, v)
    return fields


def _field(fields: dict, *names: str) -> str:
    normalized = {_norm(k): str(v) for k, v in fields.items() if v not in (None, "") and not _looks_like_redline_key_dump(v)}
    for name in names:
        val = normalized.get(_norm(name))
        if val:
            return val
    for name in names:
        want = _norm(name)
        if not want or len(want) < 5:
            continue
        # Prefer suffix/exact-ish matches before broad containment.
        for key, value in sorted(normalized.items(), key=lambda item: len(item[0])):
            if key.endswith(want):
                return value
        for key, value in sorted(normalized.items(), key=lambda item: len(item[0])):
            if want in key:
                return value
    return ""


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).lower())


def _timecode_to_frame(tc: str, fps: float) -> int:
    parts = re.split(r"[:;.]", tc.strip())
    if len(parts) != 4:
        return 0
    h, m, s, f = [int(p) for p in parts]
    return ((h * 3600 + m * 60 + s) * int(round(fps))) + f


def _frame_to_timecode(frame: int, fps: float, drop: bool = False) -> str:
    # Non-drop math by design here; enough for summary End TC when container provides start + frames.
    fps_i = max(1, int(round(fps)))
    h = frame // (fps_i * 3600); frame %= fps_i * 3600
    m = frame // (fps_i * 60); frame %= fps_i * 60
    s = frame // fps_i; f = frame % fps_i
    sep = ";" if drop else ":"
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{f:02d}"


# ── Generic ffprobe / ExifTool helpers ───────────────────────────────────────

def _run_json_tool(cmd: list[str], timeout: float = 60.0):
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        raise RuntimeError((proc.stderr or "").strip() or f"Command failed: {' '.join(cmd)}")
    payload = (proc.stdout or "").strip()
    if not payload:
        raise RuntimeError(f"No JSON output from {' '.join(cmd)}")
    return json.loads(payload)


def _ffprobe_metadata(path: Path, ffprobe: str) -> dict:
    return _run_json_tool([ffprobe, "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)])


def _exiftool_metadata(path: Path, exiftool: str) -> dict:
    data = _run_json_tool([exiftool, "-json", "-G", "-n", str(path)])
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return {}


def _first_video_stream(data: dict) -> dict:
    streams = data.get("streams") or []
    for stream in streams:
        if isinstance(stream, dict) and stream.get("codec_type") == "video":
            return stream
    return streams[0] if streams and isinstance(streams[0], dict) else {}


def _parse_float(value) -> Optional[float]:
    text = str(value or "").strip()
    if not text:
        return None
    if "/" in text:
        left, right = text.split("/", 1)
        try:
            den = float(right)
            return float(left) / den if den else None
        except Exception:
            return None
    try:
        return float(text)
    except Exception:
        match = re.search(r"\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else None


def _find_timecode_in_ffprobe(data: dict) -> str:
    candidates = []
    def collect(obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                if "timecode" in str(key).lower() or "time_code" in str(key).lower():
                    candidates.append(str(value))
                collect(value)
        elif isinstance(obj, list):
            for item in obj:
                collect(item)
    collect(data)
    for value in candidates:
        match = re.search(r"\d{2}:\d{2}:\d{2}[:;.]\d{2}", value)
        if match:
            return normalize_tc(match.group(0))
    return ""


def _row_from_generic(path: Path, ffprobe_data: Optional[dict], exif_data: Optional[dict], tool_label: str, metadata_type: str) -> dict:
    row = _blank_row(path, "Generic Video", metadata_type, tool_label)
    video = _first_video_stream(ffprobe_data or {})
    fmt = (ffprobe_data or {}).get("format") or {}
    tags = {}
    if isinstance(fmt.get("tags"), dict):
        tags.update(fmt.get("tags"))
    if isinstance(video.get("tags"), dict):
        tags.update(video.get("tags"))
    if exif_data:
        tags.update(exif_data)

    fps = _parse_float(video.get("avg_frame_rate") or video.get("r_frame_rate") or tags.get("VideoFrameRate") or tags.get("FrameRate"))
    duration = str(fmt.get("duration") or video.get("duration") or tags.get("Duration") or "")
    frames = str(video.get("nb_frames") or tags.get("FrameCount") or "")
    if not frames and fps and duration:
        try:
            frames = str(int(round(float(duration) * fps)))
        except Exception:
            pass
    width = video.get("width") or tags.get("ImageWidth") or tags.get("SourceImageWidth") or ""
    height = video.get("height") or tags.get("ImageHeight") or tags.get("SourceImageHeight") or ""
    start_tc = _find_timecode_in_ffprobe(ffprobe_data or {}) or _field(tags, "TimeCode", "StartTimeCode", "StartTimecode", "MediaStartTimeCode")
    end_tc = _field(tags, "EndTimeCode", "LastTimeCode", "MediaEndTimeCode")
    if start_tc and not end_tc and fps and frames:
        try:
            start_frame = _timecode_to_frame(start_tc, fps)
            end_tc = _frame_to_timecode(start_frame + int(float(frames)) - 1, fps, drop=";" in start_tc)
        except Exception:
            pass
    row.update({
        "ltc_in": start_tc,
        "ltc_out": end_tc,
        "start_tc": start_tc,
        "end_tc": end_tc,
        "duration": duration,
        "fps": _format_rate(fps) if fps else "",
        "frame_count": frames,
        "resolution": f"{width}x{height}" if width and height else "",
        "codec": str(video.get("codec_name") or video.get("codec_long_name") or _field(tags, "CompressorName", "CodecID") or ""),
        "camera_model": _field(tags, "Model", "CameraModelName", "DeviceModelName", "Make"),
        "serial_number": _field(tags, "SerialNumber", "CameraSerialNumber", "DeviceSerialNumber"),
        "reel": _field(tags, "ReelName", "TapeName", "ClipReelName"),
        "clip_id": _field(tags, "ClipID", "ClipName", "Title"),
        "creation_date": str(fmt.get("tags", {}).get("creation_time", "") if isinstance(fmt.get("tags"), dict) else "") or _field(tags, "CreateDate", "CreationDate"),
    })
    if not start_tc:
        row["warnings"] = "No embedded timecode found"
    return row


# ── High-level extraction ────────────────────────────────────────────────────

def _blank_row(path: Path, family: str, metadata_type: str, tool: str) -> dict:
    return {field: "" for field in CSV_FIELDS} | {
        "status": "OK",
        "camera_family": family,
        "camera": parse_camera_label(path.name),
        "file": path.name,
        "source_file": str(path),
        "metadata_type": metadata_type,
        "tool": tool,
    }


def _discover_files(source_root: Path, source_type: str) -> list[Path]:
    source_root = Path(source_root).expanduser().resolve()
    if source_root.is_file():
        candidates = [source_root]
    else:
        candidates = [p for p in source_root.rglob("*") if p.is_file()]
    selected: list[Path] = []
    for path in candidates:
        suffix = path.suffix.lower()
        if source_type == "red" and suffix in RED_EXTENSIONS:
            selected.append(path)
        elif source_type == "generic" and suffix in GENERIC_MEDIA_EXTENSIONS and suffix not in RED_EXTENSIONS:
            selected.append(path)
        elif source_type == "auto" and (suffix in RED_EXTENSIONS or suffix in GENERIC_MEDIA_EXTENSIONS):
            selected.append(path)
    return sorted(selected, key=lambda p: str(p).lower())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", errors="replace")


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _merge_redline_fields(field_sets: list[dict[str, str]]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for fields in field_sets:
        for key, value in (fields or {}).items():
            key = str(key or "").strip()
            value = str(value or "").strip()
            if not key or not value or _looks_like_redline_key_dump(value):
                continue
            existing = merged.get(key, "")
            if not existing or _looks_like_redline_key_dump(existing):
                merged[key] = value
    return merged


def _combine_dimensions(fields: dict[str, str]) -> str:
    direct = _field(fields, "Resolution", "Record Resolution", "Image Size", "Frame Size", "FrameGuideSize")
    if direct:
        match = re.search(r"\b\d{3,5}\s*[xX]\s*\d{3,5}\b", direct)
        return re.sub(r"\s+", "", match.group(0)) if match else direct
    width = _field(fields, "Width", "Frame Width", "Image Width", "Source Image Width")
    height = _field(fields, "Height", "Frame Height", "Image Height", "Source Image Height")
    if width and height:
        wm = re.search(r"\d{3,5}", width)
        hm = re.search(r"\d{3,5}", height)
        if wm and hm:
            return f"{wm.group(0)}x{hm.group(0)}"
    return ""


def _run_redline_sidecar(redline: str, path: Path, mode: str, sidecar_dir: Optional[Path], tmp_dir: Path, keep_sidecars: bool, metadata_type: str) -> Optional[Path]:
    persist = bool(sidecar_dir and (keep_sidecars or metadata_type in {"RED Per-Frame / Lens Metadata", "RED Gyro / IMU Metadata", "Raw Metadata Export"}))
    out_dir = sidecar_dir if persist and sidecar_dir else tmp_dir
    out_csv = out_dir / f"{path.stem}_printMeta{mode}.csv"
    run_redline(redline, path, out_csv, mode=mode)
    return out_csv if out_csv.exists() else None


def _red_row(path: Path, redline: str, metadata_type: str, raw_dir: Optional[Path], keep_sidecars: bool) -> dict:
    row = _blank_row(path, "RED", metadata_type, "REDline")
    sidecar_dir = raw_dir / "RED" if raw_dir else None
    mode_fields: dict[str, str] = {}
    per_frame_summary: dict[str, str] = {}
    warnings: list[str] = []

    tmp_ctx = tempfile.TemporaryDirectory(prefix="mediarunner_red_meta_")
    try:
        tmp_dir = Path(tmp_ctx.name)

        # RED documents printMeta modes 0 through 6. We sample every mode and
        # condense the useful scalar fields into one row instead of exposing raw
        # RED/RCP metadata-key dumps in custom report columns.
        parsed_sets: list[dict[str, str]] = []
        if metadata_type in {"Clip Metadata", "Raw Metadata Export", "RED Gyro / IMU Metadata"}:
            for mode in ("0", "1", "2", "3", "4"):
                try:
                    payload = _run_redline_capture(redline, path, mode)
                    if sidecar_dir and (keep_sidecars or metadata_type == "Raw Metadata Export"):
                        _write_text(sidecar_dir / f"{path.stem}_printMeta{mode}.txt", payload)
                    parsed_sets.append(parse_redline_fields(payload))
                except Exception as exc:
                    warnings.append(f"printMeta {mode} skipped: {exc}")
            mode_fields = _merge_redline_fields(parsed_sets)

        if metadata_type in {"Timecode Summary", "Clip Metadata", "RED Per-Frame / Lens Metadata", "RED Gyro / IMU Metadata", "Raw Metadata Export"}:
            try:
                per_frame_csv = _run_redline_sidecar(redline, path, "5", sidecar_dir, tmp_dir, keep_sidecars, metadata_type)
                if per_frame_csv:
                    per_frame_summary = summarize(per_frame_csv, path)
            except Exception as exc:
                warnings.append(f"printMeta 5 skipped: {exc}")

            # Mode 6 is per-frame external metadata. It is not normally needed for
            # scalar report columns, but running/saving it when available keeps the
            # REDline condensation pass complete and gives raw exports access to it.
            try:
                _run_redline_sidecar(redline, path, "6", sidecar_dir, tmp_dir, keep_sidecars, metadata_type)
            except Exception as exc:
                # Missing external metadata is common and should not fail a report.
                msg = str(exc).strip()
                if msg:
                    warnings.append(f"printMeta 6 unavailable: {msg}")
    finally:
        tmp_ctx.cleanup()

    fps_value = per_frame_summary.get("fps", "") or _parse_rate(_field(
        mode_fields, "Frame Rate", "FrameRate", "Project Frame Rate", "ProjectFrameRate",
        "Sensor Frame Rate", "FPS", "Recording Frame Rate"
    ))
    frame_count = per_frame_summary.get("frame_count", "") or _field(mode_fields, "Frame Count", "FrameCount", "Total Frames", "TotalFrames")
    resolution = _combine_dimensions(mode_fields)
    start_tc = per_frame_summary.get("ltc_in", "") or normalize_tc(_field(mode_fields, "Timecode In", "Start Timecode", "Start TC", "LTC In", "Abs Timecode", "Edge Timecode", "Timecode"))
    end_tc = per_frame_summary.get("ltc_out", "") or normalize_tc(_field(mode_fields, "Timecode Out", "End Timecode", "End TC", "LTC Out", "Last Timecode"))

    row.update({
        "ltc_in": start_tc,
        "ltc_out": end_tc,
        "start_tc": start_tc,
        "end_tc": end_tc,
        "fps": fps_value,
        "frame_count": frame_count,
        "resolution": resolution,
        "codec": "R3D",
        "camera_model": _field(mode_fields, "Camera Model", "CameraModel", "Camera Type", "CameraType", "Model", "Make"),
        "serial_number": _field(mode_fields, "Camera Serial Number", "CameraSerialNumber", "Serial Number", "SerialNumber", "Brain Serial Number", "Camera PIN", "PIN"),
        "reel": _field(mode_fields, "Reel Name", "ReelName", "Reel", "Magazine", "Roll", "Tape Name"),
        "clip_id": _field(mode_fields, "Clip Name", "ClipName", "Clip ID", "ClipID", "RDC", "Filename", "File Name"),
        "creation_date": _field(mode_fields, "Creation Date", "Create Date", "CreateDate", "Record Date", "RecordDate", "Date Recorded", "Clip Date", "Date"),
    })
    if warnings:
        row["warnings"] = "; ".join(dict.fromkeys(warnings))[:1200]
    return row


def _generic_row(path: Path, tools: dict[str, Optional[str]], metadata_type: str, raw_dir: Optional[Path]) -> dict:
    ffprobe = tools.get("ffprobe")
    exiftool = tools.get("exiftool")
    ffprobe_data = None
    exif_data = None
    warnings = []
    if ffprobe:
        try:
            ffprobe_data = _ffprobe_metadata(path, ffprobe)
            if raw_dir:
                _write_json(raw_dir / "FFprobe" / f"{path.stem}_ffprobe.json", ffprobe_data)
        except Exception as exc:
            warnings.append(f"ffprobe failed: {exc}")
    else:
        warnings.append("ffprobe not configured")
    if exiftool:
        try:
            exif_data = _exiftool_metadata(path, exiftool)
            if raw_dir:
                _write_json(raw_dir / "ExifTool" / f"{path.stem}_exiftool.json", exif_data)
        except Exception as exc:
            warnings.append(f"ExifTool failed: {exc}")
    else:
        warnings.append("ExifTool not configured")
    if ffprobe_data is None and exif_data is None:
        raise RuntimeError("No generic metadata tool produced usable output. Configure ffprobe and/or ExifTool.")
    tool_label = "+".join(name for name, present in [("ffprobe", ffprobe_data is not None), ("ExifTool", exif_data is not None)] if present)
    row = _row_from_generic(path, ffprobe_data, exif_data, tool_label, metadata_type)
    extra_warning = "; ".join(warnings)
    if extra_warning:
        row["warnings"] = "; ".join(filter(None, [row.get("warnings", ""), extra_warning]))
    return row


def _write_master_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in CSV_FIELDS})


def _metadata_report_logo_tag() -> str:
    # Use the canonical UI/metadata logo asset.
    asset_root = Path(__file__).parent / "assets"
    logo_candidates = [
        asset_root / "UIandMetadata_Logo.png",
        asset_root / "UIandMetadata_Logo.PNG",
        Path(__file__).parent / "MediaRunner_LOGO.png",
        Path(__file__).parent / "MediaRunner_LOGO.PNG",
        Path(__file__).parent / "MediaRunner_LOGO_HTML.png",
    ]
    for logo in logo_candidates:
        if logo.exists():
            try:
                import base64
                b64 = base64.b64encode(logo.read_bytes()).decode()
                return f'<img src="data:image/png;base64,{b64}" class="logo" alt="MediaRunner">'
            except Exception:
                continue
    return ""


def _write_html_report(path: Path, rows: list[dict], source_root: Path, metadata_type: str, tools: dict[str, Optional[str]]) -> None:
    ok = sum(1 for r in rows if r.get("status") == "OK")
    fail = sum(1 for r in rows if r.get("status") != "OK")
    tool_lines = "".join(
        f"<li><b>{html.escape(k)}</b>: {html.escape(v or 'Not configured')}</li>" for k, v in tools.items()
    )
    body_rows = []
    for r in rows:
        status_class = "ok" if r.get("status") == "OK" else "fail"
        body_rows.append(
            "<tr>" + "".join(
                f"<td class='{status_class if field == 'status' else ''}'>{html.escape(str(r.get(field, '')))}</td>"
                for field in ["status", "camera_family", "file", "ltc_in", "ltc_out", "fps", "resolution", "codec", "tool", "warnings"]
            ) + "</tr>"
        )
    logo_tag = _metadata_report_logo_tag()
    html_text = f"""<!doctype html>
<html><head><meta charset='utf-8'><title>MediaRunner Metadata Report</title>
<style>
body {{ background:#0E141B; color:#E6EEF5; font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif; margin:32px; }}
.card {{ background:#151E27; border:1px solid #2B3A48; border-radius:16px; padding:20px; margin-bottom:18px; }}
.header {{ display:flex; align-items:center; gap:20px; }}
.logo {{ width:214px; max-height:150px; object-fit:contain; flex:0 0 auto; background:transparent; }}
h1 {{ margin:0 0 8px 0; font-size:28px; }}
.muted {{ color:#91A1B2; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th,td {{ border-bottom:1px solid #2B3A48; padding:8px 10px; text-align:left; vertical-align:top; }}
th {{ color:#B9C7D5; text-transform:uppercase; font-size:11px; letter-spacing:.8px; }}
.ok {{ color:#7FD49A; font-weight:800; }} .fail {{ color:#E87979; font-weight:800; }}
</style></head><body>
<div class='card'>
<div class='header'>{logo_tag}<div>
<h1>MediaRunner Metadata Report</h1>
<div class='muted'>Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div>
</div></div>
<p><b>Source:</b> {html.escape(str(source_root))}<br><b>Metadata Type:</b> {html.escape(metadata_type)}<br><b>Summary:</b> {ok} OK / {fail} FAIL / {len(rows)} total</p>
<ul>{tool_lines}</ul>
</div>
<div class='card'><table><thead><tr>{''.join(f'<th>{h}</th>' for h in ['Status','Family','File','TC In','TC Out','FPS','Resolution','Codec','Tool','Warnings'])}</tr></thead><tbody>
{''.join(body_rows)}
</tbody></table></div>
</body></html>"""
    path.write_text(html_text, encoding="utf-8")


def process_metadata(
    source_root: Path,
    output_folder: Path,
    *,
    source_type: str = "auto",
    metadata_type: str = "Timecode Summary",
    keep_sidecars: bool = False,
    save_raw: bool = False,
    tool_config: Optional[dict] = None,
    progress_callback: Optional[ProgressCallback] = None,
    log_callback: Optional[LogCallback] = None,
) -> MetadataRunResult:
    source_root = Path(source_root).expanduser().resolve()
    output_folder = Path(output_folder).expanduser().resolve()
    output_folder.mkdir(parents=True, exist_ok=True)
    normalized_source_type = {
        "Auto Detect": "auto",
        "RED / R3D": "red",
        "Generic MOV / MP4 / MXF": "generic",
        "auto": "auto", "red": "red", "generic": "generic",
    }.get(source_type, "auto")
    files = _discover_files(source_root, normalized_source_type)
    if not files:
        raise RuntimeError(f"No supported media files found in {source_root}")

    cfg = tool_config or {}
    tools = resolve_tools_from_config(cfg)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    master_csv = output_folder / f"MediaRunner_Metadata_Summary_{ts}.csv"
    report_html = output_folder / f"MediaRunner_Metadata_Report_{ts}.html"
    raw_dir = output_folder / "Raw_Metadata" / ts if (save_raw or keep_sidecars or metadata_type in {"Raw Metadata Export", "RED Per-Frame / Lens Metadata", "RED Gyro / IMU Metadata"}) else None
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    total = len(files)
    for index, path in enumerate(files, start=1):
        suffix = path.suffix.lower()
        try:
            if suffix in RED_EXTENSIONS:
                redline = tools.get("redline")
                if not redline:
                    raise RuntimeError("REDline not configured or not found")
                row = _red_row(path, redline, metadata_type, raw_dir, keep_sidecars or save_raw)
            else:
                if metadata_type in {"RED Per-Frame / Lens Metadata", "RED Gyro / IMU Metadata"}:
                    row = _blank_row(path, "Generic Video", metadata_type, "")
                    row["status"] = "FAIL"
                    row["warnings"] = "RED per-frame / gyro metadata only applies to R3D clips"
                else:
                    row = _generic_row(path, tools, metadata_type, raw_dir if save_raw else None)
            rows.append(row)
            if log_callback:
                log_callback(f"{row.get('status','OK')}: {path.name} · {row.get('tool','')}")
        except Exception as exc:
            row = _blank_row(path, "RED" if suffix in RED_EXTENSIONS else "Generic Video", metadata_type, "")
            row["status"] = "FAIL"
            row["warnings"] = str(exc)
            rows.append(row)
            if log_callback:
                log_callback(f"FAIL: {path.name} · {exc}")
        if progress_callback:
            progress_callback(index, total, path, rows[-1])

    _write_master_csv(master_csv, rows)
    _write_html_report(report_html, rows, source_root, metadata_type, tools)
    return MetadataRunResult(master_csv=master_csv, report_html=report_html, rows=rows, raw_dir=raw_dir)


def run_meta(r3d_root: Path, per_frame_dir: Path, manifest: Manifest, master_csv: Path):
    result = process_metadata(
        r3d_root,
        master_csv.parent,
        source_type="red",
        metadata_type="Timecode Summary",
        keep_sidecars=True,
        save_raw=False,
        log_callback=print,
    )
    for row in result.rows:
        manifest.write(
            method="Metadata",
            source_path=row.get("source_file", ""),
            destination_path=str(result.master_csv.parent),
            camera=row.get("camera", ""),
            file=row.get("file", ""),
            note=f"LTC {row.get('ltc_in','')}→{row.get('ltc_out','')} {row.get('fps','')}fps",
            status=row.get("status", ""),
        )
    # Preserve legacy caller expectation of the requested master path.
    if result.master_csv != master_csv:
        master_csv.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result.master_csv, master_csv)


if __name__ == "__main__":
    print("\n=== MediaRunner Metadata ===\n")
    if len(sys.argv) >= 3:
        source = Path(sys.argv[1]).expanduser().resolve()
        output = Path(sys.argv[2]).expanduser().resolve()
    else:
        source = Path(input("Source folder: ").strip()).expanduser().resolve()
        output = Path(input("Output folder: ").strip()).expanduser().resolve()
    result = process_metadata(source, output, source_type="auto", metadata_type="Timecode Summary", keep_sidecars=False, save_raw=False, log_callback=print)
    print(f"\nMaster CSV: {result.master_csv}")
    print(f"Report: {result.report_html}")
