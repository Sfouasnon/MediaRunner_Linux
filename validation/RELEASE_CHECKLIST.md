# MediaRunner Release Checklist

Before a candidate release:

- [ ] `python3 verify_install.py` passes.
- [ ] `python3 validation/run_validation_suite.py --profile quick --work-dir ./validation_runs/quick_candidate` passes.
- [ ] `python3 validation/run_validation_suite.py --profile extended --work-dir ./validation_runs/extended_candidate` passes.
- [ ] Stress profile passes with an agreed run count.
- [ ] Fresh `.app` build launches.
- [ ] Latest diagnostics bundle can be created.
- [ ] Manual field tests are logged separately.
- [ ] RED Wireless tests are marked beta until real-camera sessions pass.
