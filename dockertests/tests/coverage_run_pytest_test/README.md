# coverage run pytest replay regression

Manual end-to-end reproducer for the design-partner `coverage run -m pytest`
flow.

This scenario is expected to pass `dryrun` and `record`, then hang/fail during
`replay` until Retrace handles coverage.py tracing as replay-safe control-plane
work.

Run:

```bash
cd /path/to/retracesoftware/dockertests
RETRACE_PIPELINE_TIMEOUT_SEC=120 python run.py coverage_run_pytest_test
```

Current failing signature:

```text
dryrun passes
record passes
replay hangs until the harness timeout
```

The smaller unit regression is:

```bash
python -m pytest tests/install/external/test_coverage_run_replay_regression.py -ra
```
