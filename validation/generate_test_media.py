#!/usr/bin/env python3
"""Generate deterministic MediaRunner validation media without running tests."""
from __future__ import annotations

import argparse
from pathlib import Path

from run_validation_suite import create_test_media, human_size


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate deterministic validation media.")
    parser.add_argument("output", help="Output source-media folder")
    parser.add_argument("--seed", type=int, default=5700)
    args = parser.parse_args()
    out = Path(args.output).expanduser().resolve()
    total = create_test_media(out, seed=args.seed)
    print(f"Generated {human_size(total)} at {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
