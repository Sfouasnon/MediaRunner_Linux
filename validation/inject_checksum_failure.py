#!/usr/bin/env python3
"""Flip one byte in a copied file to confirm verification fails."""
from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Intentionally corrupt one file in a destination tree.")
    parser.add_argument("destination", help="Destination folder containing copied files")
    parser.add_argument("--file", help="Specific file to corrupt. Defaults to first file found under destination.")
    args = parser.parse_args()
    root = Path(args.destination).expanduser().resolve()
    target = Path(args.file).expanduser().resolve() if args.file else next((p for p in sorted(root.rglob("*")) if p.is_file()), None)
    if target is None or not target.exists():
        raise SystemExit(f"No file found to corrupt under {root}")
    with target.open("r+b") as handle:
        first = handle.read(1)
        handle.seek(0)
        handle.write(bytes([(first[0] ^ 0xFF) if first else 0xFF]))
    print(f"Corrupted one byte: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
