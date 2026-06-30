# MediaRunner Validation Suite v2

Run from the MediaRunner source folder.

```zsh
python3 validation/run_validation_suite.py --work-dir ./validation_runs/quick_001 --profile quick
python3 validation/run_validation_suite.py --work-dir ./validation_runs/extended_001 --profile extended
python3 validation/run_validation_suite.py --work-dir ./validation_runs/stress_001 --profile stress --runs 10
```

Profiles:
- `quick`: core transfer, checksum, report, cascade, second-pass, skip-existing, corruption detection.
- `extended`: quick plus empty folder, long/nested/special-character paths, missing destination detection, truncated file detection, manifest/report audit.
- `stress`: extended plus repeated single-destination, second-pass, and corruption-detection cycles.

The suite uses synthetic deterministic media and does not include real camera or drive-disconnect testing.
