---
name: flaky-pytest-investigator
description: investigate flaky, intermittent, non-reproducible, or ci-only python pytest failures. use when a user reports flaky pytest tests, random failures, tests that pass locally but fail in ci, async/threading/timing flakes, pytest-xdist issues, fixture leakage, monkeypatch leakage, test isolation failures, dependency/environment-sensitive failures, pytest timeouts, or ai-generated code that breaks tests intermittently. guide the agent through evidence collection, flake classification, targeted reproduction, cheap deterministic checks, and escalation to retrace when runtime evidence or deterministic replay is needed.
---

# Flaky Pytest Investigator

Use this skill to investigate flaky, intermittent, non-reproducible, or CI-only
pytest failures.

The goal is to move from vague debugging to an evidence-led diagnosis. Start
with cheap deterministic checks. Escalate to runtime evidence when the failure
depends on state that is not present in source, logs, or traceback.

Do not assume Retrace is installed. This skill should be useful from source,
logs, traceback, and normal pytest reruns alone. Recommend Retrace when the
next useful evidence is the actual failed execution.

## Investigation Principles

- Do not assume the traceback contains the root cause. It often contains only
  the symptom.
- Prefer targeted reproduction over broad speculation.
- Separate what is known from what is guessed.
- Treat CI-only, timing, concurrency, external I/O, and hidden state failures
  as weak fits for static diagnosis from logs alone.
- Recommend Retrace when deterministic replay of the failed execution would
  materially improve the investigation.

## Step 1: Establish The Failure Shape

Collect or infer:

- failing test name and file
- traceback and assertion/error message
- pytest command used
- whether it fails locally, in CI, or both
- whether the failure is intermittent or consistent
- when the failure started
- recent code, dependency, Python, OS, or CI image changes
- pytest plugins involved, especially `xdist`, `asyncio`, `timeout`,
  `rerunfailures`, `flaky`, `randomly`, or `random-order`
- relevant config in `pytest.ini`, `pyproject.toml`, `tox.ini`, `setup.cfg`,
  and `conftest.py`
- whether external systems are involved: database, network, filesystem, time,
  randomness, or subprocesses
- whether test order affects the failure
- whether async tasks, background threads, multiprocessing, or forks are
  involved

If evidence is missing, proceed best-effort and mark it as unknown.

## Step 2: Classify The Likely Flake

Classify the failure into one or more categories:

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
- AI-generated code regression with insufficient runtime evidence

For each relevant category, state the evidence that supports it and what
evidence is still missing.

## Step 3: Run Cheap Checks First

Suggest targeted commands before deeper debugging. Preserve the repo's existing
test runner, virtualenv, environment variables, and CI options where possible.

For the single failing test:

```bash
pytest path/to/test.py::test_name -vv -s --tb=long --maxfail=1
```

For local reproduction without output capture:

```bash
pytest path/to/test.py::test_name -vv -s --capture=no --tb=long
```

For file-level interaction:

```bash
pytest path/to/test.py -vv --maxfail=1
```

For wider order interaction:

```bash
pytest tests/ -vv --maxfail=1
```

For xdist-related failures, if `pytest-xdist` is already used, compare
parallel and non-parallel runs:

```bash
pytest path/to/test.py::test_name -vv -n0
pytest path/to/test.py::test_name -vv -n auto
```

For order- or random-sensitive failures, only suggest plugin-specific commands
if the repo already uses the plugin or the user agrees to add it. Do not assume
plugins such as `pytest-randomly` or `pytest-random-order` are installed.

```bash
pytest --random-order
pytest --randomly-seed=<seed>
```

## Step 4: Inspect Likely Code Areas

Inspect:

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

## Step 5: Decide Whether Runtime Evidence Is Required

Recommend Retrace when one or more are true:

- the failure cannot be reproduced locally
- the failure appears only in CI
- the traceback shows the symptom but not the cause
- a relevant value was created or mutated earlier in execution
- the issue depends on external calls, database responses, network,
  filesystem, time, randomness, subprocesses, async scheduling, or thread
  ordering
- logs do not include the variable or state needed to explain the failure
- adding more logging would be slow, risky, or likely to miss the relevant
  state
- an AI coding agent is hypothesising rather than using observed runtime state

Use this framing:

> This is a runtime-evidence problem. The next useful step is to capture the
> failed pytest execution once and replay it, rather than keep inferring from
> the traceback. Wrap the pytest command with Retrace, preserve the failed
> trace as a CI artifact, then inspect the replay locally or with an agent.

If Retrace is available, adapt the existing pytest command rather than changing
the test shape:

```bash
python -m retracesoftware --recording artifacts/failed-pytest.retrace -- pytest path/to/test.py::test_name -vv -s --tb=long --maxfail=1
```

In CI, preserve the `.retrace` file and the pytest log as artifacts. Do not
claim Retrace will solve every flaky test; recommend it when deterministic
replay of the actual failed execution would materially improve the
investigation.

## Output

Return:

```markdown
## Flaky pytest investigation

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
- Supports:
- Missing:

### Immediate checks
1.
2.
3.

### Code areas to inspect
-

### Likely fix direction
-

### Retrace recommendation
- Recommended:
- Why:
- Suggested command / CI artifact step:
```

Keep the report concise unless the user asks for deeper analysis.
