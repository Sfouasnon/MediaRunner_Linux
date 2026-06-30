# MediaRunner Version 0.3.0-beta (V2 — Production Hardening)

Source package for MediaRunner. This V2 tree applies the full June 2026
robustness audit (findings #1–17) on top of 0.2.81-beta. See
`PACKAGE_NOTES.md` for the complete change list.

## Version 0.3.0-beta highlights

- Bounded retries with exponential backoff on every copy and FTP operation; `retry_count` is now recorded in manifests.
- FTP downloads resume interrupted `.part` files via REST instead of restarting from byte 0.
- Dropped camera connections reconnect and continue; NOOP keepalives between clips.
- Local transfers are cancellable (Stop button) and the app warns before quitting mid-job, then shuts workers down cleanly.
- Rotating application log + crash diagnostics in `~/.mediarunner/logs/` (`mediarunner.log`, `crash.log`).
- Source files are hashed during the copy read (one source read instead of two per destination).
- Disk-space preflight aggregates per physical volume with a 2% headroom margin; disk-full aborts the job instead of failing file-by-file.
- Orphaned `.part` files are excluded from discovery/verification and swept at job start.
- Manifest finalization and network-config saves are atomic (temp file + rename).
- FTP credentials/camera map are snapshotted per job so Settings edits can't change a running transfer.
- Anchored reel matching (007 no longer matches 1007), REDline timeout, clip-range input validation, capped UI tables.
- Networking scans now separate FTP transfer readiness from RCP2 visibility. Configured cameras are probed on FTP/FTPS media access and RCP2 WebSocket identity, and unknown RED cameras can be discovered by active local-subnet RCP2 scans.
- RCP2 ports are configurable in Networking: TCP defaults to 9998, and the SDK UDP discovery port defaults to 1112. Full UDP discovery requires the RED RCP SDK packet flow; MediaRunner uses the RCP2 WebSocket identity scan as the pure-Python fallback.

### Credentials note

FTP credentials are stored in plain text in `~/.mediarunner/network_config.json`
(permissions 0600) and TLS certificate verification is disabled for camera
connections. This is acceptable on a closed camera network but do not reuse
these credentials elsewhere.

## Quick run

```bash
cd /path/to/MediaRunner_Linux
python3 verify_install.py
python3 mediarunner_gui.py
```

## Build Linux executable

```bash
cd /path/to/MediaRunner_Linux
./build_linux.sh
./dist/MediaRunner/MediaRunner
```

## Run validation

```bash
cd /path/to/MediaRunner_Linux
python3 validation/run_validation_suite.py --profile stress --runs 10 --work-dir ./validation_runs/stress_manual_001
xdg-open ./validation_runs/stress_manual_001/validation_report.html
```

## Custom report workflow

```text
Reports → Use Latest Transfer → choose/load/design template → Generate from Selected Source
```

Alternate path:

```text
Reports → select a transfer HTML or MediaRunner_Manifest CSV → choose/load/design template → Generate from Selected Source
```

The exported files are written next to the source CSV in a `Custom_Reports` folder. Custom reports are media-only by default: `.R3D`, `.MOV`, `.MXF`, `.MP4`, `.BRAW`, `.ARI`, `.ARX`, `.CRM`, `.WAV`, `.AIFF`, and `.AIF` rows are included; sidecars/control/report rows are hidden.
