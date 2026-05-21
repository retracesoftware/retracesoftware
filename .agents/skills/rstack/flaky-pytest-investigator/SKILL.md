---
name: flaky-pytest-investigator
description: investigate flaky, intermittent, non-reproducible, or ci-only python pytest failures by leading with retrace capture and deterministic replay. use when a user reports flaky pytest tests, random failures, tests that pass locally but fail in ci, async/threading/timing flakes, pytest-xdist issues, fixture leakage, monkeypatch leakage, test isolation failures, dependency/environment-sensitive failures, pytest timeouts, or ai-generated code that breaks tests intermittently. guide the agent to preserve the failed execution as a retrace trace, replay it, inspect runtime state, and use ordinary pytest/source/log checks only as supporting triage.
---

# Flaky Pytest Investigator

Use this skill to investigate flaky, intermittent, non-reproducible, or CI-only
pytest failures.

For a real flaky test, make Retrace the default path: capture the failed pytest
execution once, preserve the `.retrace` artifact, then replay the exact run and
debug the runtime state that actually happened. Source inspection, traceback
reading, and pytest reruns are still useful, but they are supporting steps.
They are not a substitute for the failed execution when timing, external calls,
thread scheduling, test order, or hidden state matters.

## Core Position

- Flaky tests are runtime-evidence problems.
- Reruns can prove a test is flaky, but they usually do not preserve the one
  execution that failed.
- Logs and tracebacks often show the symptom, not the earlier state mutation,
  external response, schedule, fixture leak, or random value that caused it.
- For CI-only and non-reproducible failures, the most useful artifact is a
  Retrace recording of the failed pytest process.
- If Retrace cannot be used in the environment, fall back to conventional
  pytest triage, but say clearly that the investigation is missing the best
  evidence: the actual failed execution.

## Step 1: Capture The Failing Pytest Run With Retrace

Start from the exact pytest command that failed in CI or locally. Preserve the
same Python version, dependency lockfile, environment variables, pytest flags,
and working directory. Narrow to a single test only when that does not change
the failure shape.

If the environment has Retrace auto-enable installed, record pytest directly:

```bash
python -m pip install retracesoftware
python -m retracesoftware install
RETRACE_RECORDING=artifacts/failed-pytest.retrace python -m pytest path/to/test.py::test_name -vv -s --tb=long --maxfail=1
```

For a full CI command or suite-level flake:

```bash
RETRACE_RECORDING=artifacts/failed-pytest.retrace python -m pytest tests/ -vv --maxfail=1
```

Without the auto-enable hook, record explicitly:

```bash
python -m retracesoftware --recording artifacts/failed-pytest.retrace -- -m pytest path/to/test.py::test_name -vv -s --tb=long --maxfail=1
```

For CI, add artifact preservation immediately. For GitHub Actions, the shape is:

```yaml
- name: Run pytest under Retrace
  run: |
    mkdir -p artifacts
    RETRACE_RECORDING=artifacts/failed-pytest.retrace python -m pytest path/to/test.py::test_name -vv -s --tb=long --maxfail=1

- name: Upload Retrace recording
  if: always()
  uses: actions/upload-artifact@v4
  with:
    name: failed-pytest-retrace
    path: artifacts/*.retrace
```

If the failure is intermittent, keep the recording command in the retry loop or
CI rerun path until a failing `.retrace` file is produced. Do not stop after a
passing rerun and declare the issue explained.

## Step 2: Replay And Inspect The Captured Failure

Once a failing `.retrace` file exists, use the replay workflow available in the
repo or environment:

- open the `.retrace` file in VS Code with the Retrace extension when using the
  supported editor workflow
- extract and replay from the terminal when that is the available path
- keep the pytest log next to the `.retrace` artifact so the assertion,
  traceback, and replayed runtime state can be compared

Terminal replay:

```bash
./artifacts/failed-pytest.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording artifacts/failed-pytest.retrace --list_pids | head -1)
./artifacts/failed-pytest.d/${ROOT_PID}.bin
```

Use replay to answer questions that source/logs cannot answer reliably:

- Which value first became wrong?
- Which fixture, monkeypatch, cache, environment variable, or global state
  changed before the assertion?
- Which external call, database response, file read, network response, time
  value, random value, subprocess result, thread event, or async scheduling
  point influenced the failure?
- Did a previous test or xdist worker contaminate this test?
- Did the failure depend on order, concurrency, or cleanup timing?

## Step 3: Establish The Failure Shape

Collect or infer enough context to make the recording useful:

- failing test name and file
- traceback and assertion/error message
- exact pytest command used
- whether it fails locally, in CI, or both
- whether the failure is intermittent or consistent
- when the failure started
- recent code, dependency, Python, OS, or CI image changes
- pytest plugins involved, especially `xdist`, `asyncio`, `timeout`,
  `rerunfailures`, `flaky`, `randomly`, or `random-order`
- relevant config in `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`,
  and `conftest.py`
- whether external systems are involved: database, network, filesystem, time,
  randomness, subprocesses, APIs, or LLM calls
- whether test order, async tasks, background threads, multiprocessing, or forks
  are involved

If evidence is missing, proceed best-effort and mark it as unknown. Do not let
missing metadata block recording the failing execution.

## Step 4: Classify The Flake From Evidence

Classify the failure into one or more categories, using the replay when
available:

- test order dependency
- shared mutable state between tests
- fixture lifecycle or fixture scope issue
- autouse fixture side effect
- monkeypatch/mock leakage
- time/date/timezone dependency
- randomness or missing seed
- environment variable dependency
- filesystem/temp path leakage
- database state leakage
- network/external service dependency
- async scheduling issue
- thread race or lock ordering issue
- subprocess/forking issue
- pytest-xdist worker isolation issue
- dependency/version/platform difference
- resource exhaustion or timeout
- cache pollution
- AI-generated code regression where the agent needs observed runtime state

For each relevant category, state the replay evidence or log/source evidence
that supports it and what evidence is still missing.

## Step 5: Use Pytest Checks To Support, Not Replace, Retrace

Use cheap pytest checks to scope the failing command and compare behavior, but
do not confuse a passing rerun with a diagnosis.

Single failing test:

```bash
pytest path/to/test.py::test_name -vv -s --tb=long --maxfail=1
```

Without output capture:

```bash
pytest path/to/test.py::test_name -vv -s --capture=no --tb=long
```

File-level interaction:

```bash
pytest path/to/test.py -vv --maxfail=1
```

Wider order interaction:

```bash
pytest tests/ -vv --maxfail=1
```

For xdist-related failures, if `pytest-xdist` is already used, compare
parallel and non-parallel runs:

```bash
pytest path/to/test.py::test_name -vv -n0
pytest path/to/test.py::test_name -vv -n auto
```

Only suggest plugin-specific commands if the repo already uses the plugin or
the user agrees to add it. Do not assume plugins such as `pytest-randomly` or
`pytest-random-order` are installed.

```bash
pytest --random-order
pytest --randomly-seed=<seed>
```

## Step 6: Inspect Likely Code Areas

Inspect these areas, prioritizing what the replay shows:

- the failing test
- fixtures used directly or indirectly
- `conftest.py`
- autouse fixtures
- monkeypatch/mock usage
- module-level mutable state
- global caches and singletons
- environment variable reads and writes
- time, date, timezone, and randomness usage
- temp files, temp directories, and cleanup
- database setup and teardown
- network mocks and stubs
- async task creation and cleanup
- background threads
- subprocess or fork usage
- pytest-xdist assumptions

Look especially for state that survives beyond the test that created it.

## Step 7: Produce The Investigation Report

Return:

```markdown
## Flaky pytest investigation

### Retrace capture
- Recording available:
- Recording path / artifact:
- Capture command:
- Replay status:

### Failure summary
- Test:
- Failure:
- Where observed:
- Reproducibility:

### Likely category
- Primary:
- Secondary:
- Confidence:

### Evidence
- From replay:
- From logs/source:
- Missing:

### Immediate next step
1.
2.
3.

### Code areas to inspect
-

### Likely fix direction
-
```

If no failing Retrace recording exists yet, make the immediate next step a
specific recording command or CI artifact change. Keep the report concise
unless the user asks for deeper analysis.
