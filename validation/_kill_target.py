#!/usr/bin/env python3
"""Kill-test target: run a verified local transfer until done or SIGKILLed.

Usage: python3 _kill_target.py <src_root> <dst_root> <manifest_csv>

The parent stress harness SIGKILLs this process at a random moment and then
asserts the on-disk invariants (no corrupt committed files, only .part
remnants, parseable manifest). Exit code 0 = all files verified.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediarunner_core import Manifest  # noqa: E402
from mediarunner_transfer import discover_files, transfer_file  # noqa: E402

# Throttle (seconds per copy chunk) so the parent's SIGKILL reliably lands
# mid-transfer on fast SSDs. Slowing the copy does not weaken the invariant
# being proven — the kill still interrupts at an arbitrary syscall.
THROTTLE = float(os.environ.get("MEDIARUNNER_KILL_THROTTLE", "0.05"))


def main() -> int:
    src_root = Path(sys.argv[1])
    dst_root = Path(sys.argv[2])
    manifest = Manifest(Path(sys.argv[3]))
    lock = threading.Lock()
    throttle = (lambda _n: time.sleep(THROTTLE)) if THROTTLE > 0 else None
    ok = True
    for f, cam, reel, clip in discover_files(src_root, []):
        dst = dst_root / f.relative_to(src_root)
        if not transfer_file(f, dst, manifest, cam, reel, clip, True, lock,
                             progress_callback=throttle):
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
