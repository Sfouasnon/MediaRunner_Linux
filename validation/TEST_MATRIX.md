# MediaRunner Test Matrix

## Automated local validation

| Profile | Command | Coverage |
|---|---|---|
| Quick | `python3 validation/run_validation_suite.py --profile quick --work-dir ./validation_runs/quick_001` | Core copy/checksum/report invariants. |
| Extended | `python3 validation/run_validation_suite.py --profile extended --work-dir ./validation_runs/extended_001` | Quick plus empty folder, long paths, special characters, missing file, truncated file, manifest/report audit. |
| Stress | `python3 validation/run_validation_suite.py --profile stress --runs 10 --work-dir ./validation_runs/stress_001` | Extended plus repeated regression cycles. |
| **Field stress** | `python3 validation/run_validation_suite.py --profile stress-field --work-dir ./validation_runs/field_001` | Fault-injection proof of the resilience layer (requires `pip3 install pyftpdlib`). |

## Field-stress scenarios (automated)

| Scenario | Failure injected | Proves |
|---|---|---|
| `ftp_drop_resume` | Data connection killed mid-file (every file, once) | Retry → reconnect → REST resume from the partial; all files verify |
| `ftp_reject_rest` | Server replies 502 to REST with `.part` files pre-seeded | Clean fallback to full restart; still verifies |
| `ftp_stall_timeout` | Server hangs past the client timeout | Timeout → reconnect → recover |
| `kill_mid_transfer` | `SIGKILL` mid-copy, N iterations (env `MEDIARUNNER_STRESS_KILL_RUNS`) | No corrupt committed file ever; manifest stays parseable; rerun converges to fully verified |
| `enospc_abort` | ENOSPC injected into the `.part` writer | `FatalTransferError` raised (job-level abort), nothing committed, no futile retries |
| `cancel_fuzzer` | Cancel at random instants, N iterations (env `MEDIARUNNER_STRESS_FUZZ_RUNS`) | Only Verified/Cancelled outcomes; every committed file byte-perfect |

The kill test throttles the target copy (`MEDIARUNNER_KILL_THROTTLE`, default 0.05 s/chunk)
so the SIGKILL reliably lands mid-transfer on fast SSDs; a run where nothing was
actually killed counts as a FAIL rather than a hollow pass.

## Manual production validation still required

- Full-card real media transfers.
- Physical destination unplug during copy (automated ENOSPC/kill tests approximate but do not replace this).
- Permission denied destination.
- RED Wireless Ingest with real RED cameras (the fault server emulates the protocol, not RED firmware).
- Long-duration / multi-terabyte soak jobs (watch memory + descriptor counts).
