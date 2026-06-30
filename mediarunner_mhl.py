#!/usr/bin/env python3
from __future__ import annotations

"""ASC MHL parsing helpers used for FTP source verification."""

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional
import xml.etree.ElementTree as ET

SUPPORTED_MHL_ALGORITHMS = {"md5", "sha1", "sha256", "xxh64", "xxh128"}
PREFERRED_MHL_ALGORITHMS = ("sha256", "xxh128", "xxh64", "sha1", "md5")
_IGNORED_HASH_CHILDREN = {"path", "previouspath", "metadata"}


def _local_name(tag: object) -> str:
    text = str(tag or "")
    return text.rsplit("}", 1)[-1]


def normalize_mhl_path(value: str) -> str:
    text = str(value or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    text = text.lstrip("/")
    return "/".join(part for part in text.split("/") if part and part != ".")


@dataclass(frozen=True)
class MHLHashRecord:
    algorithm: str
    value: str
    action: str = ""
    hash_date: str = ""


@dataclass(frozen=True)
class MHLFileRecord:
    mhl_file: str
    relative_path: str
    file_name: str
    size_bytes: Optional[int] = None
    last_modification_date: str = ""
    hashes: tuple[MHLHashRecord, ...] = ()

    @property
    def usable_hashes(self) -> tuple[MHLHashRecord, ...]:
        return tuple(
            item for item in self.hashes
            if item.value and str(item.action or "").strip().lower() != "failed"
        )


def find_mhl_files(clip_dir: Path) -> list[Path]:
    clip_dir = Path(clip_dir)
    return sorted(path for path in clip_dir.rglob("*.mhl") if path.is_file())


def parse_mhl_file(path: Path) -> list[MHLFileRecord]:
    path = Path(path)
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Invalid ASC MHL XML: {exc}") from exc

    records: list[MHLFileRecord] = []
    for elem in root.iter():
        if _local_name(elem.tag).lower() != "hash":
            continue
        path_elem = next((child for child in list(elem) if _local_name(child.tag).lower() == "path"), None)
        rel_path = normalize_mhl_path(path_elem.text if path_elem is not None else "")
        if not rel_path:
            continue
        size_bytes: Optional[int] = None
        last_modification_date = ""
        if path_elem is not None:
            size_attr = str(path_elem.attrib.get("size", "") or "").strip()
            if size_attr.isdigit():
                size_bytes = int(size_attr)
            last_modification_date = str(path_elem.attrib.get("lastmodificationdate", "") or "").strip()
        hashes: list[MHLHashRecord] = []
        for child in list(elem):
            name = _local_name(child.tag).lower()
            if name in _IGNORED_HASH_CHILDREN:
                continue
            value = str(child.text or "").strip()
            if not value:
                continue
            hashes.append(MHLHashRecord(
                algorithm=name,
                value=value,
                action=str(child.attrib.get("action", "") or "").strip().lower(),
                hash_date=str(child.attrib.get("hashdate", "") or "").strip(),
            ))
        records.append(MHLFileRecord(
            mhl_file=str(path),
            relative_path=rel_path,
            file_name=Path(rel_path).name,
            size_bytes=size_bytes,
            last_modification_date=last_modification_date,
            hashes=tuple(hashes),
        ))
    return records


def load_clip_mhl(clip_dir: Path) -> tuple[list[Path], list[MHLFileRecord], list[str]]:
    mhl_files = find_mhl_files(clip_dir)
    records: list[MHLFileRecord] = []
    errors: list[str] = []
    for mhl_file in mhl_files:
        try:
            records.extend(parse_mhl_file(mhl_file))
        except Exception as exc:
            errors.append(f"{mhl_file}: {exc}")
    return mhl_files, records, errors


def _candidate_paths(local_file: Path, clip_dir: Path) -> tuple[str, str, str]:
    relative = normalize_mhl_path(local_file.relative_to(clip_dir).as_posix())
    file_name = normalize_mhl_path(local_file.name)
    clip_prefixed = normalize_mhl_path(f"{clip_dir.name}/{relative}") if relative else file_name
    return relative, file_name, clip_prefixed


def find_matching_mhl_record(local_file: Path, clip_dir: Path, records: Iterable[MHLFileRecord]) -> Optional[MHLFileRecord]:
    relative, file_name, clip_prefixed = _candidate_paths(Path(local_file), Path(clip_dir))
    best: Optional[tuple[int, MHLFileRecord]] = None
    for record in records:
        record_path = normalize_mhl_path(record.relative_path)
        score = 0
        if record_path == relative:
            score = 100
        elif record_path == clip_prefixed:
            score = 95
        elif record_path.endswith(f"/{relative}") and relative:
            score = 90
        elif record.file_name == file_name:
            score = 80
        elif record_path.endswith(f"/{file_name}") and file_name:
            score = 70
        if score and (best is None or score > best[0]):
            best = (score, record)
    return best[1] if best else None


def select_preferred_hash(record: MHLFileRecord) -> tuple[Optional[MHLHashRecord], list[str]]:
    usable = list(record.usable_hashes or record.hashes)
    by_algorithm = {item.algorithm.lower(): item for item in usable if item.value}
    for algorithm in PREFERRED_MHL_ALGORITHMS:
        if algorithm in by_algorithm:
            return by_algorithm[algorithm], []
    unknown = sorted({item.algorithm.lower() for item in usable if item.value})
    return None, unknown
