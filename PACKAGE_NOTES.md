# MediaRunner 0.3.0-beta (V2)

Production-hardening release. Applies all 17 findings from the June 2026
robustness audit (`MediaRunner_Audit_2026-06-10.md` in the V1 folder) on top of
0.2.81-beta. No feature behavior was removed; all changes target reliability
under sustained high-resolution, multi-destination transfers.

## Changes by audit finding

- **#1 Retries** — `retry_operation()` in `mediarunner_core.py` wraps local copies, FTP downloads, listings, and remote hashing with bounded exponential-backoff retries (default 3 attempts; `MEDIARUNNER_RETRY_ATTEMPTS` / `MEDIARUNNER_RETRY_BACKOFF` env overrides). Manifest `retry_count` is now populated.
- **#2 FTP resume** — `_ftp_download_file` (array path) and `download_remote_file` (RED Wireless) resume existing `.part` files via FTP REST, falling back to a clean restart if the server rejects REST. Partial files are kept on failure so a retry continues instead of restarting.
- **#3 Reconnect** — camera workers hold connections in a reconnectable holder; a dropped control connection mid-reel reconnects and continues with the remaining clips. NOOP keepalives run between clips/files.
- **#4 Local cancellation** — `TransferPage` now has a Stop button wired through `transfer_file` → `copy_file_to_part`; cancelled files are recorded as `Cancelled` in manifests.
- **#5 Close guard** — quitting with a job running prompts for confirmation, sets cancel events, and joins workers for up to 5 s before exit.
- **#6 Logging** — new `mediarunner_logging.py`: rotating log + `crash.log` (faulthandler) in `~/.mediarunner/logs/`, plus global and per-thread exception hooks. Wired into all entry points.
- **#7 Hash-during-copy** — `copy_file_to_part_with_hash` computes source checksums during the copy read; verified transfers now read the source once instead of twice per destination.
- **#8 Disk-space hardening** — capacity preflight aggregates required bytes per physical volume (st_dev) with a 2% headroom margin; ENOSPC mid-job raises `FatalTransferError` and aborts the whole job.
- **#9 .part hygiene** — `.part` files are excluded from source discovery and FTP clip verification; `cleanup_stale_parts()` sweeps orphans at local-job start.
- **#10 Atomic writes** — `finalize_ftp_manifest` and `save_network_config` write to a temp file and rename.
- **#11 Config snapshot** — `ftp_settings_snapshot()` captures credentials/cameras once per job; Settings edits no longer affect running transfers.
- **#12 Anchored matching** — reel matching uses delimiter-anchored regex (007 ≠ 1007).
- **#13 REDline timeout** — `run_redline` now has a 300 s timeout.
- **#14 Input validation** — `parse_clip_numbers` raises operator-readable errors; the FTP page validates clip ranges before starting the worker.
- **#15 xxhash fallback** — missing xxhash logs a loud error once; file I/O errors are no longer swallowed by the fallback.
- **#16 Credentials** — storage/TLS posture documented in README.
- **#17 UI tables** — result tables cap at 5,000 rows (CSV manifest remains the full record).

## Added after initial V2 (field-stress + UX round)

- Byte-based live progress bar (smooth during large files), payload preview on source selection, em-dash for empty report fields, root-level files no longer leak the filename into the Camera column.
- Settings: configurable log folder + Open Logs; engineering password gate — the Validation page is hidden until unlocked (salted sha256 hash in config, re-locks on relaunch).
- **Field-stress harness** (`--profile stress-field`, needs `pip3 install pyftpdlib`): fault-injection FTP server (drop / stall / REST-reject), SIGKILL crash-atomicity test, ENOSPC injection, cancellation fuzzer. See TEST_MATRIX.md.
- Fixed: `transfer_file` no longer swallows `FatalTransferError` (disk-full now aborts the whole job on every code path).
- `design/MediaRunner_UI_Concept.html` — clickable production-grade UI concept (unified FTP page, engineering-locked diagnostics).

## Concept UI port (in-app)

- Palette/typography/chips/nav ported from `design/MediaRunner_UI_Concept.html` into the Qt app.
- Nav: Dashboard | **Offload** (was Transfer) | **FTP** (Camera Array / Single Camera segmented modes, replacing the separate RED Wireless page) | Metadata | Reports | **Networking** (was FTP Settings) | Settings | Validation (engineering-locked, hidden until unlocked).
- Offload page: source → destinations lane with arrow, segmented Strategy / Verification / Scope controls (Inline / Deferred pass / Off), Start / Stop / Resume.
- Sidebar: compact brand header — existing MediaRunner logo unchanged, wordmark + version beside it.
- All transfer/FTP/wireless logic untouched: the existing pages are wrapped and re-skinned, not rewritten.

## Not included (deferred architecture items)

Engine extraction from the GUI, central job queue, session resume journal,
ASC MHL use on the RED Wireless path, and ASC MHL generation remain open as
upgrade items A–F in the audit.

## Recommended test

1. `python3 verify_install.py`
2. `python3 validation/run_validation_suite.py --profile extended --work-dir ./validation_runs/v2_001`
3. Launch `python3 mediarunner_gui.py`; start a local transfer and confirm the Stop button cancels it and `~/.mediarunner/logs/mediarunner.log` records the run.
4. Field-test an FTP pull with a forced Wi-Fi drop to observe reconnect + resume.
