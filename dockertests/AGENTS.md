# Docker Test Harness

This directory is the scenario/integration harness for Retrace. Use it to
reproduce user-visible failures in a controlled container environment, then
isolate the owning bug into the narrowest runtime layer or Python test bucket.

## Current Core Files

- `run.py`
  Discovers tests, filters by name/tags, excludes perf tests by default, and
  invokes `runtest.sh` per scenario.
- `runtest.sh`
  Runs one scenario through phase-based pipelines and reports the failed phase.
- `tests/*/`
  Per-scenario directories containing `test.py`, optional `requirements.txt`,
  optional `docker-compose.yml`, optional `client.py`, and optional `tags`.

## Mental Model

- Dockertests are for realistic reproduction, not the first place to encode
  every regression permanently.
- The failed phase matters. A scenario that fails in `install` or `dryrun` is
  not the same class of bug as one that fails in `replay`.
- Script tests and server tests use different compose pipelines:
  script tests use `install -> dryrun -> record -> replay -> cleanup`, while
  server tests add `server-dryrun` and `server-record` phases.
- The harness intentionally isolates packages, images, recordings, and compose
  projects per test so stale artifacts should not bleed across runs.
- After reproducing a real product bug here, prefer isolating it into `tests/`
  or the owning subsystem unless the scenario itself is the contract.

## High-Risk Areas

- Misclassifying environment or Docker-only failures as product regressions.
- Ignoring the failed phase and debugging the wrong layer.
- Breaking the record/replay pipeline by changing compose services, volumes, or
  cleanup expectations without checking both script and server flows.
- Package cache, recording-dir, or compose-project isolation regressions that
  make scenarios contaminate one another.
- Scenario drift when CLI flags, replay entrypoints, or Go replay behavior
  change but the harness still assumes old behavior.

## Working Rules

- Reproduce here first when the bug is scenario-dependent, then narrow it down.
- If the failure is phase-specific, say which phase owns the problem before
  recommending changes.
- For server-style scenarios, distinguish `server-dryrun` / `server-record`
  failures from normal `dryrun` / `record` failures; they often point at
  different lifecycle or transport assumptions.
- Prefer fixing product code or lower-level tests over making the scenario more
  permissive, unless the harness itself is wrong.
- If you change replay CLI behavior or extraction layout, check whether
  `runtest.sh`, `run.py`, or scenario helpers need updating in the same diff.
- Keep scenario-specific dependencies inside each test directory rather than
  pushing them into global harness behavior unnecessarily.
- If a failure only appears under local Docker or sandbox restrictions, call
  that out clearly instead of treating it as a replay contract regression.

## References

- `dockertests/README.md`
- `dockertests/run.py`
- `dockertests/runtest.sh`
- `dockertests/base-requirements.txt`
