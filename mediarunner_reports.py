#!/usr/bin/env python3
"""MediaRunner custom report templates and export helpers."""
from __future__ import annotations

import base64
import csv
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

try:
    from mediarunner_core import CONFIG_DIR, transfer_status_bucket
except Exception:  # pragma: no cover - fallback for direct tooling
    CONFIG_DIR = Path.home() / ".mediarunner"
    def transfer_status_bucket(status):  # type: ignore
        text = str(status or "").strip()
        return "ok" if text == "Verified" else "fail" if text in {"FAIL", "ERROR", "MISSING"} else "warn"

CUSTOM_REPORT_TEMPLATE_PATH = CONFIG_DIR / "custom_report_templates.json"


@dataclass(frozen=True)
class ReportField:
    key: str
    label: str
    aliases: tuple[str, ...]
    description: str = ""


def _n(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def human_size(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        num = float(text)
    except Exception:
        return text
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if num < 1024:
            return f"{num:.2f} {unit}" if unit != "B" else f"{int(num)} {unit}"
        num /= 1024.0
    return f"{num:.2f} PB"


FIELD_REGISTRY: list[ReportField] = [
    ReportField("status", "Status", ("status", "transfer_status", "result"), "Verification or extraction status."),
    ReportField("method", "Method", ("method", "stage", "metadata_type", "transfer_method"), "Transfer or metadata method."),
    ReportField("camera", "Camera", ("camera", "camera_label", "cam"), "Camera unit label parsed from clip/file name when available."),
    ReportField("camera_type", "Camera Type", ("mr_camera_type", "camera_type", "camera_model", "camera_family", "model", "make"), "Camera family/model from REDline, ffprobe, or ExifTool when available."),
    ReportField("serial_number", "Serial Number", ("mr_serial_number", "serial_number", "serial", "camera_serial", "cameraserialnumber"), "Camera/device serial number when available."),
    ReportField("creation_date", "Creation Date", ("mr_creation_date", "creation_date", "created_at", "create_date", "creation_time", "media_create_date", "record_date", "date_recorded"), "Clip/media creation date when available. Transfer timestamps are not used as clip creation dates."),
    ReportField("reel", "Reel", ("mr_reel", "reel", "reel_name", "reelname", "magazine", "roll"), "Reel / roll identifier."),
    ReportField("clip", "Clip", ("mr_clip", "clip_id", "clipid", "clip_name", "clipname", "clip"), "Clip identifier."),
    ReportField("file_name", "File Name", ("file", "filename", "file_name", "name", "source_file"), "Media file name."),
    ReportField("relative_path", "Relative Path", ("relative_path", "path", "source_file", "source_path"), "Path from the source root when available."),
    ReportField("source_path", "Source Path", ("source_path", "source_file", "source", "src"), "Source path used by transfer/metadata."),
    ReportField("destination_path", "Destination Path", ("destination_path", "destination", "dst", "dest"), "Destination path used by transfer."),
    ReportField("file_size", "File Size", ("size_human", "file_size", "size", "size_bytes", "bytes"), "Human readable file size."),
    ReportField("source_size", "Source Size", ("source_size",), "Source file size when available."),
    ReportField("destination_size", "Destination Size", ("destination_size",), "Destination file size when available."),
    ReportField("src_hash", "Source Checksum", ("src_hash", "source_hash", "source_checksum", "checksum_source"), "Source checksum."),
    ReportField("dst_hash", "Destination Checksum", ("dst_hash", "destination_hash", "dest_hash", "checksum_destination"), "Destination checksum."),
    ReportField("xxhash", "XXHash", ("xxhash",), "Best available xxhash checksum."),
    ReportField("sha256", "SHA-256", ("sha256",), "Best available SHA-256 checksum."),
    ReportField("md5", "MD5", ("md5",), "Best available MD5 checksum."),
    ReportField("checksum_algorithm", "Checksum Algorithm", ("checksum_algorithm",), "Algorithms recorded for this row."),
    ReportField("verification_time", "Verification Time", ("verification_time",), "Timestamp when verification status was written."),
    ReportField("verification_status", "Verification Status", ("verification_status",), "Normalized verification status."),
    ReportField("verification_source", "Verification Source", ("verification_source",), "What verified the row: ASC MHL, local checksum only, mismatch, or another policy."),
    ReportField("mhl_path", "MHL Path", ("mhl_path",), "ASC MHL file used for verification when available."),
    ReportField("mhl_algorithm", "MHL Algorithm", ("mhl_algorithm",), "ASC MHL hash algorithm used for verification."),
    ReportField("mhl_expected_hash", "MHL Expected Hash", ("mhl_expected_hash",), "Expected hash recorded in ASC MHL."),
    ReportField("mhl_actual_hash", "MHL Actual Hash", ("mhl_actual_hash",), "Hash computed locally for ASC MHL comparison."),
    ReportField("mhl_verified", "MHL Verified", ("mhl_verified",), "Whether ASC MHL verification matched for this row."),
    ReportField("checksum", "Checksum", ("checksum", "src_hash", "dst_hash"), "Best available checksum."),
    ReportField("retry_count", "Retry Count", ("retry_count",), "Retry count when available."),
    ReportField("timecode_in", "Timecode In", ("mr_timecode_in", "ltc_in", "start_tc", "timecode_in", "tc_in", "timecodein"), "Start / LTC in timecode."),
    ReportField("timecode_out", "Timecode Out", ("mr_timecode_out", "ltc_out", "end_tc", "timecode_out", "tc_out", "timecodeout"), "End / LTC out timecode."),
    ReportField("duration", "Duration", ("mr_duration", "duration", "clip_duration"), "Duration when available from metadata."),
    ReportField("fps", "Frame Rate", ("mr_fps", "fps", "frame_rate", "framerate"), "Frame rate."),
    ReportField("frame_count", "Frame Count", ("mr_frame_count", "frame_count", "frames", "framecount"), "Frame count."),
    ReportField("resolution", "Resolution", ("mr_resolution", "resolution", "image_size", "framesize"), "Frame size/resolution."),
    ReportField("codec", "Codec", ("mr_codec", "codec", "codec_name", "format"), "Codec/container codec."),
    ReportField("tool", "Metadata Tool", ("mr_tool", "tool", "metadata_tool"), "Tool used for extraction."),
    ReportField("warnings", "Warnings", ("mr_warnings", "warnings", "warning", "errors"), "Warnings captured during extraction."),
    ReportField("error", "Error", ("error",), "Transfer or verification error when available."),
    ReportField("note", "Note", ("note", "notes", "message"), "Manifest note or transfer message."),
]

FIELD_BY_KEY = {field.key: field for field in FIELD_REGISTRY}
FIELD_KEYS = [field.key for field in FIELD_REGISTRY]

BUILT_IN_TEMPLATES: dict[str, list[str]] = {
    "Standard Verification Report": ["status", "method", "camera", "reel", "clip", "file_name", "file_size", "src_hash", "dst_hash", "note"],
    "Camera Department Report": ["camera_type", "creation_date", "reel", "clip", "file_name", "timecode_in", "timecode_out", "fps", "resolution", "codec"],
    "Timecode Report": ["reel", "clip", "file_name", "timecode_in", "timecode_out", "duration", "fps", "frame_count"],
    "Producer / Post Handoff": ["status", "camera", "reel", "clip", "file_name", "file_size", "creation_date", "destination_path"],
    "Full Metadata Export": FIELD_KEYS,
}


def load_user_templates(path: Optional[Path] = None) -> dict[str, list[str]]:
    path = Path(path or CUSTOM_REPORT_TEMPLATE_PATH).expanduser()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        clean: dict[str, list[str]] = {}
        for name, columns in data.items():
            if isinstance(columns, list):
                keys = [str(c).strip() for c in columns if str(c).strip() in FIELD_BY_KEY]
                if keys:
                    clean[str(name).strip() or "Custom Template"] = keys
        return clean
    except Exception:
        return {}


def save_user_template(name: str, columns: Iterable[str], path: Optional[Path] = None) -> Path:
    name = str(name or "").strip() or "Custom Template"
    keys = parse_column_list(columns)
    if not keys:
        raise ValueError("A custom report template needs at least one valid field.")
    path = Path(path or CUSTOM_REPORT_TEMPLATE_PATH).expanduser()
    templates = load_user_templates(path)
    templates[name] = keys
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(templates, indent=2, sort_keys=True), encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path


def all_templates() -> dict[str, list[str]]:
    merged = dict(BUILT_IN_TEMPLATES)
    merged.update(load_user_templates())
    return merged


def parse_column_list(columns: Iterable[str] | str) -> list[str]:
    if isinstance(columns, str):
        raw = re.split(r"[,\n]+", columns)
    else:
        raw = list(columns)
    keys: list[str] = []
    alias_to_key: dict[str, str] = {}
    for field in FIELD_REGISTRY:
        alias_to_key[_n(field.key)] = field.key
        alias_to_key[_n(field.label)] = field.key
        for alias in field.aliases:
            alias_to_key[_n(alias)] = field.key
    for item in raw:
        item = str(item or "").strip()
        key = alias_to_key.get(_n(item), item if item in FIELD_BY_KEY else "")
        if key and key in FIELD_BY_KEY and key not in keys:
            keys.append(key)
    return keys


def label_for(key: str) -> str:
    return FIELD_BY_KEY.get(key, ReportField(key, key.replace("_", " ").title(), (key,))).label


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    path = Path(path).expanduser()
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as f:
        reader = csv.DictReader(f)
        return [{str(k or "").strip(): (v.strip() if isinstance(v, str) else "") for k, v in row.items()} for row in reader]


def _row_index(row: dict) -> dict[str, str]:
    return {_n(k): str(v or "") for k, v in row.items() if str(k or "").strip()}


def _direct(row: dict, *aliases: str) -> str:
    idx = _row_index(row)
    for alias in aliases:
        value = idx.get(_n(alias), "")
        if value:
            return value
    for alias in aliases:
        want = _n(alias)
        if not want:
            continue
        for key, value in idx.items():
            if want in key and value:
                return value
    return ""


def value_for(row: dict, key: str) -> str:
    field = FIELD_BY_KEY.get(key)
    aliases = field.aliases if field else (key,)
    value = _direct(row, *aliases)
    if key == "file_name":
        value = Path(value).name if value and ("/" in value or "\\" in value) else value
    elif key == "relative_path":
        source = _direct(row, "relative_path")
        if source:
            value = source
        elif value:
            value = Path(value).name if Path(value).is_absolute() else value
    elif key == "file_size":
        value = human_size(value)
    elif key == "checksum":
        src = _direct(row, "src_hash", "source_hash", "source_checksum")
        dst = _direct(row, "dst_hash", "destination_hash", "dest_hash", "destination_checksum")
        value = src or dst or value
    elif key == "camera_type":
        # Do not fall back to the transfer manifest's generic "camera" field here;
        # on flat RED card copies that field can be an .RDC package name, which is
        # a clip/container identifier rather than a camera model/type.
        value = value or _direct(row, "camera_model") or _direct(row, "camera_family")
    elif key == "creation_date":
        # Intentionally avoid transfer manifest timestamp fallback. Custom reports
        # should show embedded media creation/record dates only.
        value = value
    return str(value or "")


def available_fields(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    present: list[str] = []
    sample = rows[:50]
    for field in FIELD_REGISTRY:
        if any(value_for(row, field.key) for row in sample):
            present.append(field.key)
    return present


def _logo_data_uri() -> str:
    # Retained for template compatibility; the brand mark is now inline HTML
    # (see mediarunner_core.BRAND_MARK_HTML) so every artifact matches.
    return ""


def _safe_token(value: str, fallback: str = "Custom_Report") -> str:
    token = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("_")
    return token or fallback


# Fields that benefit from reading embedded media metadata rather than only the
# transfer/checksum manifest. Manifest-only reports still remain fast.
ENRICHABLE_FIELDS = {
    "camera_type", "serial_number", "creation_date", "reel", "clip",
    "timecode_in", "timecode_out", "duration", "fps", "frame_count",
    "resolution", "codec", "tool", "warnings",
}
TOOL_REQUIRED_FIELDS = {
    "camera_type", "serial_number", "creation_date", "timecode_in",
    "timecode_out", "duration", "fps", "frame_count", "resolution",
}
GENERIC_MEDIA_EXTENSIONS_FALLBACK = {
    ".mov", ".mp4", ".m4v", ".mxf", ".avi", ".mkv", ".webm",
    ".braw", ".ari", ".arx", ".crm", ".wav", ".aiff", ".aif",
}
RED_EXTENSIONS_FALLBACK = {".r3d"}
CUSTOM_REPORT_MEDIA_EXTENSIONS = {
    ".r3d", ".mov", ".mxf", ".mp4", ".braw", ".ari", ".arx",
    ".crm", ".wav", ".aiff", ".aif", ".m4v", ".avi", ".mkv", ".webm",
}
SIDECAR_EXTENSIONS = {
    ".rtn", ".rmd", ".r3m", ".xml", ".ale", ".csv", ".json",
    ".txt", ".thm", ".lrv", ".xmp", ".md5", ".html", ".htm",
    ".log", ".pdf", ".rdc",
}
ROW_EXTENSION_RE = re.compile(r'(?i)\.([A-Za-z][A-Za-z0-9]{1,5})(?=$|[\s,;:)\]\}"\']|[/\\])')


def _existing_file(path: Path | str | None) -> Optional[Path]:
    if not path:
        return None
    try:
        p = Path(str(path)).expanduser()
        return p.resolve() if p.exists() and p.is_file() else None
    except Exception:
        return None


def _row_extensions(row: dict) -> set[str]:
    """Return file-like extensions referenced anywhere in a manifest row.

    The classifier intentionally lets true media extensions win over RED package
    folder/sidecar extensions. A RED .R3D row may also contain an .RDC package
    path; that row should still be reported because the actual row target is a
    media segment.
    """
    exts: set[str] = set()
    for value in row.values():
        text = str(value or "")
        if not text:
            continue
        # Path.suffix catches the common case where the cell is exactly a file
        # path or file name. The regex catches embedded paths such as
        # CAM_A/007.RDM/A007_C060_001.RDC/A007_C060_001.R3D.
        try:
            suffix = Path(text).suffix.lower()
            if suffix:
                exts.add(suffix)
        except Exception:
            pass
        for match in ROW_EXTENSION_RE.finditer(text):
            exts.add("." + match.group(1).lower())
    return exts


def _classify_report_row(row: dict, source_csv: Path) -> str:
    """Classify a custom-report row as media, control/sidecar, or unknown."""
    exts = _row_extensions(row)
    media_exts = set(CUSTOM_REPORT_MEDIA_EXTENSIONS) | set(RED_EXTENSIONS_FALLBACK) | set(GENERIC_MEDIA_EXTENSIONS_FALLBACK)
    if exts & media_exts:
        return "media"
    if exts & SIDECAR_EXTENSIONS:
        return "hidden"

    # If the manifest row resolves to an existing file, use that file's own
    # suffix. Do not call _best_media_target() here: that function deliberately
    # maps sidecars to sibling media for enrichment, but the report filter must
    # hide sidecar/control rows before enrichment runs.
    for candidate in _candidate_media_paths(row, source_csv):
        suffix = candidate.suffix.lower()
        if suffix in media_exts:
            return "media"
        if suffix in SIDECAR_EXTENSIONS:
            return "hidden"
    return "unknown"


def filter_media_rows_for_custom_report(rows: list[dict], source_csv: Path) -> tuple[list[dict], dict[str, object]]:
    """Default custom-report filter: keep media rows, hide sidecars/control rows.

    If the source CSV has no recognizable media/control extensions at all, leave
    it untouched so older metadata-summary CSVs without file extensions still
    produce a report. If at least one media row is found, the output is strictly
    media-only and any sidecar/control/unknown rows are hidden.
    """
    total = len(rows)
    classifications = [_classify_report_row(row, source_csv) for row in rows]
    media_count = classifications.count("media")
    sidecar_count = classifications.count("hidden")
    unknown_count = classifications.count("unknown")

    if media_count > 0:
        kept = [row for row, cls in zip(rows, classifications) if cls == "media"]
        applied = True
    elif sidecar_count > 0:
        kept = []
        applied = True
    else:
        kept = rows
        applied = False

    hidden = total - len(kept)
    if applied:
        source_summary = f"{total} files scanned · {len(kept)} media files reported · {hidden} sidecar/control files hidden"
        filter_text = "Media files only. Sidecar/control files hidden before metadata enrichment."
    else:
        source_summary = f"{total} rows scanned · no media/control extensions detected, so rows were left unchanged"
        filter_text = "Media files only when file extensions are available; this source had no recognizable media/control file references."

    return kept, {
        "enabled": True,
        "applied": applied,
        "source_rows": total,
        "media_rows": len(kept),
        "hidden_rows": hidden,
        "sidecar_rows": sidecar_count,
        "unknown_rows": unknown_count,
        "summary": source_summary,
        "filter": filter_text,
    }


def _transfer_root_candidates(source_csv: Path) -> list[Path]:
    roots: list[Path] = []
    try:
        parent = source_csv.parent.resolve()
        # Transfer manifests live in _checksums; validation/manifests use similar
        # convention. The destination/media root is usually one level up.
        if parent.name.lower() in {"_checksums", "_manifests", "manifests"}:
            roots.append(parent.parent)
        roots.append(parent)
    except Exception:
        pass
    return roots


def _candidate_media_paths(row: dict, source_csv: Path) -> list[Path]:
    """Infer possible real media paths for a manifest row.

    GUI transfer manifests store source_path and destination_path as transfer roots,
    not per-file paths. This resolver combines those roots with manifest columns and
    also falls back to the manifest's sibling destination root.
    """
    candidates: list[Path] = []
    idx = _row_index(row)

    def raw(*names: str) -> str:
        for name in names:
            value = idx.get(_n(name), "")
            if value:
                return value
        return ""

    roots: list[Path] = []
    for value in [raw("destination_path", "destination", "dst", "dest"), raw("source_path", "source", "src")]:
        if not value:
            continue
        p = Path(value).expanduser()
        if p.exists() and p.is_file():
            candidates.append(p)
        elif p.exists() and p.is_dir():
            roots.append(p)
    roots.extend(_transfer_root_candidates(source_csv))

    rel = raw("relative_path", "rel_path", "path")
    file_name = raw("file", "file_name", "filename", "name")
    camera = raw("camera", "camera_label")
    reel = raw("reel", "reel_name")
    clip = raw("clip", "clip_id", "clip_name")

    rels: list[Path] = []
    for value in [rel, file_name]:
        if value:
            vp = Path(value).expanduser()
            if vp.is_absolute():
                candidates.append(vp)
            else:
                rels.append(vp)

    part_sets = []
    full = [x for x in [camera, reel, clip, file_name] if x]
    if full:
        part_sets.append(full)
    for parts in ([camera, reel, file_name], [camera, clip, file_name], [camera, file_name], [reel, clip, file_name], [clip, file_name]):
        cleaned = [x for x in parts if x]
        if cleaned and cleaned not in part_sets:
            part_sets.append(cleaned)
    for parts in part_sets:
        try:
            rels.append(Path(*parts))
        except Exception:
            pass

    seen_roots = []
    for root in roots:
        try:
            resolved = root.expanduser().resolve()
        except Exception:
            resolved = root.expanduser()
        if resolved not in seen_roots:
            seen_roots.append(resolved)
    for root in seen_roots:
        for rel_path in rels:
            candidates.append(root / rel_path)

    out: list[Path] = []
    seen: set[str] = set()
    for c in candidates:
        f = _existing_file(c)
        if f:
            key = str(f)
            if key not in seen:
                seen.add(key)
                out.append(f)
    return out


def _red_package_for(path: Path) -> Optional[Path]:
    for p in [path, *path.parents]:
        if p.suffix.lower() == ".rdc":
            return p
    return None


def _best_media_target(path: Path) -> Optional[Path]:
    suffix = path.suffix.lower()
    try:
        from mediarunner_meta import RED_EXTENSIONS, GENERIC_MEDIA_EXTENSIONS
    except Exception:
        RED_EXTENSIONS = RED_EXTENSIONS_FALLBACK
        GENERIC_MEDIA_EXTENSIONS = GENERIC_MEDIA_EXTENSIONS_FALLBACK
    if suffix in set(RED_EXTENSIONS) or suffix in set(GENERIC_MEDIA_EXTENSIONS):
        return path

    # RED sidecars inside an .RDC package inherit clip metadata from the sibling R3D.
    package = _red_package_for(path)
    if package and package.exists():
        r3ds = sorted(package.glob("*.R3D")) + sorted(package.glob("*.r3d"))
        if r3ds:
            return r3ds[0].resolve()

    # Generic safety: a sidecar next to one supported media file should use that media.
    if suffix in SIDECAR_EXTENSIONS and path.parent.exists():
        media = []
        for ext in sorted(set(GENERIC_MEDIA_EXTENSIONS) | set(RED_EXTENSIONS)):
            media.extend(path.parent.glob(f"*{ext}"))
            media.extend(path.parent.glob(f"*{ext.upper()}"))
        if media:
            return sorted(media, key=lambda p: p.name.lower())[0].resolve()
    return None


def _red_name_hints(path: Path) -> dict[str, str]:
    """Useful RED hints without requiring REDline.

    Handles package names such as G007_B064_032526.RDC and segment files such as
    G007_B064_032526_001.R3D. These are not a replacement for REDline metadata,
    but they prevent obvious blank/wrong reel/clip values when only a manifest is
    available or REDline is not installed on the tester's machine.
    """
    package = _red_package_for(path)
    stem = (package.stem if package else path.stem)
    stem = re.sub(r"_\d{3}$", "", stem)
    parts = [p for p in stem.split("_") if p]
    hints: dict[str, str] = {}
    if parts:
        hints["mr_clip"] = stem
    if len(parts) >= 2:
        hints["mr_reel"] = parts[1]
    if path.suffix.lower() == ".r3d" or package:
        hints["mr_codec"] = "R3D"
    return hints


def _looks_like_metadata_key_dump(value: str) -> bool:
    """Return True for REDline/RCP-style header dumps accidentally used as values.

    Some REDline printMeta modes can emit a very wide comma-delimited list of
    metadata keys. If that header row is mistaken for a value, custom reports end
    up showing paragraphs such as "Black X,Luma Curve...Lens...Camera..." in
    Camera Type/Reel/Clip. Those cells should be blank or use safe filename
    hints instead.
    """
    text = str(value or "").strip()
    if not text:
        return False
    comma_count = text.count(",")
    colon_count = text.count(":")
    if len(text) > 180 and comma_count >= 6:
        return True
    # Common signature of RED/RCP metadata key lists.
    key_terms = (
        "luma curve", "red curve", "lg g", "shutter", "aperture",
        "focus distance", "lens", "camera notes", "frame guide",
        "aspect ratio", "production name", "operator", "director",
    )
    term_hits = sum(1 for term in key_terms if term in text.lower())
    if comma_count >= 4 and term_hits >= 2:
        return True
    if colon_count >= 3 and comma_count >= 3 and term_hits:
        return True
    return False


def _clean_metadata_scalar(field: str, value: str) -> str:
    """Conservative cleanup for values that will be promoted to report columns."""
    text = str(value or "").strip().strip('"')
    if not text or _looks_like_metadata_key_dump(text):
        return ""

    # High-level report identity fields should be short scalar values. Reject
    # comma-heavy or paragraph-like values and let filename hints remain instead.
    if field in {"camera_type", "serial_number", "reel", "clip", "codec"}:
        if len(text) > 140 or "\n" in text or text.count(",") > 2:
            return ""

    if field in {"timecode_in", "timecode_out"}:
        match = re.search(r"\d{2}:\d{2}:\d{2}[:;.]\d{2}", text)
        return match.group(0).replace(".", ":") if match else ""

    if field == "fps":
        match = re.search(r"\d+(?:\.\d+)?", text)
        return match.group(0) if match else ""

    if field == "frame_count":
        match = re.search(r"\d+", text.replace(",", ""))
        return match.group(0) if match else ""

    if field == "resolution":
        match = re.search(r"\b\d{3,5}\s*[xX]\s*\d{3,5}\b", text)
        return re.sub(r"\s+", "", match.group(0)) if match else ""

    return text


def _metadata_value_map(meta_row: dict, target: Path) -> dict[str, str]:
    hints = _red_name_hints(target)
    mapped = dict(hints)

    def pick(field: str, *names: str) -> str:
        return _clean_metadata_scalar(field, _direct(meta_row, *names))

    camera_type = pick("camera_type", "camera_type", "camera_model", "camera_family", "model", "make")
    if camera_type:
        mapped["mr_camera_type"] = camera_type
    serial = pick("serial_number", "serial_number", "serial", "camera_serial", "cameraserialnumber")
    if serial:
        mapped["mr_serial_number"] = serial
    creation = pick("creation_date", "creation_date", "created_at", "create_date", "creation_time", "record_date", "date_recorded")
    if creation:
        mapped["mr_creation_date"] = creation
    reel = pick("reel", "reel", "reel_name", "reelname", "magazine", "roll")
    if reel:
        mapped["mr_reel"] = reel
    clip = pick("clip", "clip", "clip_id", "clipid", "clip_name", "clipname")
    if clip:
        mapped["mr_clip"] = clip
    tc_in = pick("timecode_in", "ltc_in", "start_tc", "timecode_in", "tc_in")
    if tc_in:
        mapped["mr_timecode_in"] = tc_in
    tc_out = pick("timecode_out", "ltc_out", "end_tc", "timecode_out", "tc_out")
    if tc_out:
        mapped["mr_timecode_out"] = tc_out
    for canonical, names in {
        "duration": ("duration", "clip_duration"),
        "fps": ("fps", "frame_rate", "framerate"),
        "frame_count": ("frame_count", "frames", "framecount"),
        "resolution": ("resolution", "image_size", "framesize"),
        "codec": ("codec", "codec_name", "format"),
        "tool": ("tool", "metadata_tool"),
        "warnings": ("warnings", "warning", "errors", "error"),
    }.items():
        value = pick(canonical, *names) if canonical != "warnings" else _direct(meta_row, *names)
        if value:
            mapped[f"mr_{canonical}"] = value
    mapped["mr_metadata_source"] = str(target)
    return mapped


def _extract_metadata_for_target(target: Path, selected_columns: list[str], tools: dict[str, Optional[str]]) -> tuple[dict[str, str], str, bool]:
    """Return mapped metadata, note, used_external_tool."""
    suffix = target.suffix.lower()
    need_tools = bool(set(selected_columns) & ENRICHABLE_FIELDS)
    try:
        from mediarunner_meta import _red_row, _generic_row, RED_EXTENSIONS, GENERIC_MEDIA_EXTENSIONS
    except Exception as exc:
        mapped = _red_name_hints(target)
        mapped["mr_warnings"] = f"metadata helpers unavailable: {exc}"
        return mapped, mapped["mr_warnings"], False

    try:
        if suffix in RED_EXTENSIONS:
            if need_tools and tools.get("redline"):
                meta_row = _red_row(target, tools["redline"], "Clip Metadata", None, False)
                mapped = _metadata_value_map(meta_row, target)
                return mapped, "REDline printMeta 0-6 condensed", True
            mapped = _red_name_hints(target)
            if need_tools and not tools.get("redline"):
                mapped["mr_warnings"] = "REDline not configured; RED embedded metadata not extracted"
                return mapped, mapped["mr_warnings"], False
            return mapped, "RED filename hints", False
        if suffix in GENERIC_MEDIA_EXTENSIONS:
            if tools.get("ffprobe") or tools.get("exiftool"):
                meta_row = _generic_row(target, tools, "Clip Metadata", None)
                mapped = _metadata_value_map(meta_row, target)
                return mapped, mapped.get("mr_tool", "ffprobe/ExifTool"), True
            mapped = {"mr_warnings": "ffprobe/ExifTool not configured; embedded metadata not extracted"}
            return mapped, mapped["mr_warnings"], False
        mapped = {"mr_warnings": f"unsupported metadata target: {target.name}"}
        return mapped, mapped["mr_warnings"], False
    except Exception as exc:
        mapped = _red_name_hints(target)
        mapped["mr_warnings"] = str(exc)
        return mapped, str(exc), False


def _load_metadata_tool_config() -> dict:
    try:
        from mediarunner_core import load_network_config
        return load_network_config()
    except Exception:
        return {}


def enrich_rows_with_metadata(rows: list[dict], columns: list[str], source_csv: Path, tool_config: Optional[dict] = None) -> tuple[list[dict], dict[str, object]]:
    """Best-effort enrichment for custom reports.

    Reads real media files referenced by a transfer manifest and adds mr_* fields
    that take precedence in report columns. The report still generates if tools are
    missing or individual files cannot be inspected.
    """
    if not rows or not (set(columns) & ENRICHABLE_FIELDS):
        return rows, {"enabled": False, "summary": "No embedded metadata fields selected."}

    try:
        from mediarunner_meta import resolve_tools_from_config
        tools = resolve_tools_from_config(tool_config or _load_metadata_tool_config())
    except Exception:
        tools = {"redline": None, "ffprobe": None, "exiftool": None, "ffmpeg": None}

    cache: dict[str, tuple[dict[str, str], str, bool]] = {}
    enriched_rows: list[dict] = []
    located = 0
    used_external = 0
    notes: list[str] = []

    for row in rows:
        out = dict(row)
        target: Optional[Path] = None
        for candidate in _candidate_media_paths(row, source_csv):
            target = _best_media_target(candidate)
            if target:
                break
        if not target:
            out.setdefault("mr_warnings", "No media file found for metadata enrichment")
            enriched_rows.append(out)
            continue
        located += 1
        key = str(target)
        if key not in cache:
            cache[key] = _extract_metadata_for_target(target, columns, tools)
        mapped, note, external = cache[key]
        if external:
            used_external += 1
        if note and note not in notes and len(notes) < 8:
            notes.append(note)
        # Preserve manifest values while letting value_for() prefer mr_* enriched fields.
        out.update({k: v for k, v in mapped.items() if v})
        enriched_rows.append(out)

    tool_bits = []
    for key, label in [("redline", "REDline"), ("ffprobe", "ffprobe"), ("exiftool", "ExifTool")]:
        tool_bits.append(f"{label}: {'found' if tools.get(key) else 'not found'}")
    summary = (
        f"Embedded metadata enrichment inspected {len(cache)} media target(s) for {located}/{len(rows)} row(s). "
        f"External tool reads: {used_external}. {'; '.join(tool_bits)}."
    )
    if notes:
        summary += " Notes: " + " | ".join(notes)
    return enriched_rows, {
        "enabled": True,
        "rows": len(rows),
        "located_rows": located,
        "media_targets": len(cache),
        "external_tool_reads": used_external,
        "tools": tools,
        "summary": summary,
    }


def write_custom_csv(rows: list[dict], columns: list[str], out_csv: Path) -> Path:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([label_for(c) for c in columns])
        for row in rows:
            writer.writerow([value_for(row, c) for c in columns])
    return out_csv


def write_custom_html(rows: list[dict], columns: list[str], out_html: Path, *, title: str, source_csv: Path, template_name: str, metadata_summary: Optional[dict[str, object]] = None, filter_summary: Optional[dict[str, object]] = None) -> Path:
    out_html.parent.mkdir(parents=True, exist_ok=True)
    from mediarunner_core import BRAND_MARK_HTML
    logo_tag = BRAND_MARK_HTML
    ok = sum(1 for row in rows if transfer_status_bucket(value_for(row, "verification_status") or value_for(row, "status")) == "ok")
    warn = sum(1 for row in rows if transfer_status_bucket(value_for(row, "verification_status") or value_for(row, "status")) == "warn")
    fail = sum(1 for row in rows if transfer_status_bucket(value_for(row, "verification_status") or value_for(row, "status")) == "fail")
    blank_counts = {c: sum(1 for r in rows if not value_for(r, c)) for c in columns}
    missing_summary = ", ".join(f"{label_for(c)} {blank_counts[c]}/{len(rows)} blank" for c in columns if rows and blank_counts[c])
    if not missing_summary:
        missing_summary = "All selected fields populated where source data provides them."
    enrichment_text = ""
    if metadata_summary and metadata_summary.get("enabled"):
        enrichment_text = str(metadata_summary.get("summary", "")).strip()
    elif metadata_summary:
        enrichment_text = str(metadata_summary.get("summary", "")).strip()
    filter_text = str((filter_summary or {}).get("filter", "Media files only. Sidecar/control files hidden before metadata enrichment.")).strip()
    source_summary = str((filter_summary or {}).get("summary", "")).strip()
    headers = "".join(f"<th>{html.escape(label_for(c))}</th>" for c in columns)
    body = []
    for row in rows:
        status = value_for(row, "verification_status") or value_for(row, "status")
        cls = transfer_status_bucket(status)
        body.append("<tr class='%s'>%s</tr>" % (cls, "".join(f"<td>{html.escape(value_for(row, c))}</td>" for c in columns)))
    css = """
body{background:#F7F8FA;color:#1E252B;font-family:-apple-system,BlinkMacSystemFont,'Helvetica Neue',Arial,sans-serif;margin:34px;font-size:13px}.header{display:flex;align-items:center;gap:20px;border-bottom:4px solid #111;padding-bottom:16px;margin-bottom:22px}.logo{width:220px;max-height:120px;object-fit:contain}.title h1{font-size:28px;margin:0}.title div{color:#607080;font-size:13px;margin-top:4px}.cards{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:12px;margin-bottom:18px}.card{background:#fff;border:1px solid #DCE2E8;border-radius:14px;padding:14px;box-shadow:0 1px 3px rgba(20,30,40,.05)}.num{font-size:24px;font-weight:900}.label{color:#667888;font-size:11px;text-transform:uppercase;letter-spacing:.7px;font-weight:800}.meta{background:#fff;border:1px solid #DCE2E8;border-radius:14px;padding:14px;margin:16px 0 18px;line-height:1.6}.path{font-family:Menlo,Monaco,Consolas,monospace;font-size:12px;word-break:break-all}table{border-collapse:collapse;width:100%;background:#fff;border:1px solid #DCE2E8;border-radius:14px;overflow:hidden}th{background:#172331;color:#EAF2F8;font-size:11px;text-transform:uppercase;letter-spacing:.7px;text-align:left;padding:10px}td{border-bottom:1px solid #E8EDF2;padding:9px 10px;vertical-align:top;word-break:break-word;font-size:12px}tr:nth-child(even) td{background:#FAFBFC}tr.ok td:first-child{color:#198754;font-weight:900}tr.warn td:first-child{color:#D28B18;font-weight:900}tr.fail td:first-child{color:#D64545;font-weight:900}.foot{color:#667888;margin-top:18px;font-size:12px}@media print{body{margin:18px}.card,.meta,table{box-shadow:none}.logo{width:180px}}
"""
    text = f"""<!doctype html><html><head><meta charset='utf-8'><title>{html.escape(title)}</title><style>{css}</style></head><body>
<div class='header'>{logo_tag}<div class='title'><h1>{html.escape(title)}</h1><div>Template: {html.escape(template_name)} · Generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</div></div></div>
<div class='cards'><div class='card'><div class='num'>{len(rows)}</div><div class='label'>Rows</div></div><div class='card'><div class='num'>{ok}</div><div class='label'>Verified</div></div><div class='card'><div class='num'>{warn}</div><div class='label'>Unverified</div></div><div class='card'><div class='num'>{fail}</div><div class='label'>Flagged</div></div></div>
<div class='meta'><b>Source CSV:</b> <span class='path'>{html.escape(str(source_csv))}</span><br><b>Selected columns:</b> {html.escape(', '.join(label_for(c) for c in columns))}<br><b>Media filter:</b> {html.escape(filter_text)}{('<br><b>Source summary:</b> ' + html.escape(source_summary)) if source_summary else ''}<br><b>Metadata enrichment:</b> {html.escape(enrichment_text or 'Manifest/CSV fields only; no embedded media metadata requested.')}<br><b>Missing-field summary:</b> {html.escape(missing_summary)}</div>
<table><thead><tr>{headers}</tr></thead><tbody>{''.join(body)}</tbody></table>
<div class='foot'>MediaRunner custom reports reflect the metadata available in the selected manifest/metadata CSV. Blank cells mean the selected source did not provide that field.</div>
</body></html>"""
    out_html.write_text(text, encoding="utf-8")
    return out_html


def generate_custom_report(source_csv: Path, *, output_dir: Optional[Path] = None, template_name: str = "Camera Department Report", columns: Optional[Iterable[str] | str] = None, title: Optional[str] = None) -> dict[str, object]:
    source_csv = Path(source_csv).expanduser().resolve()
    if source_csv.suffix.lower() != ".csv":
        raise ValueError("Custom reports must be generated from a CSV manifest or metadata summary.")
    rows = read_csv_rows(source_csv)
    if not rows:
        raise ValueError(f"No rows found in {source_csv}")
    rows, filter_summary = filter_media_rows_for_custom_report(rows, source_csv)
    templates = all_templates()
    selected_columns = parse_column_list(columns if columns is not None else templates.get(template_name, []))
    if not selected_columns:
        selected_columns = templates.get("Standard Verification Report", BUILT_IN_TEMPLATES["Standard Verification Report"])
    rows, metadata_summary = enrich_rows_with_metadata(rows, selected_columns, source_csv)
    output_dir = Path(output_dir or (source_csv.parent / "Custom_Reports")).expanduser().resolve()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = _safe_token(template_name)
    out_csv = output_dir / f"MediaRunner_Custom_Report_{safe_name}_{ts}.csv"
    out_html = output_dir / f"MediaRunner_Custom_Report_{safe_name}_{ts}.html"
    report_title = title or f"MediaRunner Custom Report — {template_name}"
    write_custom_csv(rows, selected_columns, out_csv)
    write_custom_html(rows, selected_columns, out_html, title=report_title, source_csv=source_csv, template_name=template_name, metadata_summary=metadata_summary, filter_summary=filter_summary)
    return {
        "html": out_html,
        "csv": out_csv,
        "rows": len(rows),
        "columns": selected_columns,
        "template": template_name,
        "available_fields": available_fields(rows),
        "metadata_summary": metadata_summary,
        "filter_summary": filter_summary,
    }


def field_help_text() -> str:
    return ", ".join(field.key for field in FIELD_REGISTRY)
