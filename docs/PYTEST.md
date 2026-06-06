# Retrace Pytest Workflow

The blessed local command is:

```bash
retrace pytest -- examples/ci_failure_demo
```

The separator is optional unless a pytest argument collides with a Retrace
wrapper option, so this also works:

```bash
retrace pytest examples/ci_failure_demo -q
```

This is a thin wrapper around the existing record command:

```bash
python -m retracesoftware \
  --recording recordings/pytest.retrace \
  --format binary \
  --stacktraces \
  -- -m pytest examples/ci_failure_demo
```

If pytest passes, `retrace pytest` deletes the passing recording by default.
If pytest fails, it keeps the recording and prints a replay command:

```bash
replay --recording recordings/pytest.retrace --workspace
```

That writes `recordings/pytest.code-workspace`, which can be opened in VS Code.

Use `--recording` to choose a different trace path:

```bash
retrace pytest --recording recordings/ci-demo.retrace -- examples/ci_failure_demo
```

Use `--keep-passing` when you want to keep a recording even if tests pass.

## Failed-test artifacts

`pytest --retrace` currently records failed tests by re-running the failed node
under the existing Retrace recorder. The child pytest process is guarded with
`RETRACE_PYTEST_RECORDING_CHILD=1` so it does not recursively create another
failed-test run.

For each failed test, Retrace writes:

```text
.retrace/runs/<run-id>/
  recording.bin
  manifest.json
  failure.txt
```

After a failure, use:

```bash
retrace runs
retrace agent-context --latest
retrace inspect --latest
```

`retrace mcp --latest` starts only when the latest run has a real, available
recording. `retrace clean --latest --yes` removes the most recent run.

## Coverage.py and CI

`pytest --retrace` is intended to work when pytest is launched through
coverage.py:

```bash
coverage run -m pytest -p retracesoftware.pytest_plugin --retrace tests/test_example.py
```

In installed environments where the plugin is auto-discovered, this is enough:

```bash
coverage run -m pytest --retrace tests/test_example.py
```

The failed-test manifest records `environment.coverage_detected` when coverage
is active. It also records `environment.ci_detected` when `CI` is present and
`environment.teamcity_detected` when `TEAMCITY_VERSION` is present. Environment
variable values are not written to `manifest.json`.

## Captured Pytest Metadata

The manifest includes:

- pytest version
- active pytest plugin names
- pytest-randomly detection and seed when available
- pytest-env detection
- pytest-sugar detection
- teamcity-messages detection
- coverage detection

These integrations are detected best-effort. Retrace does not depend on those
packages being installed.

## Compatibility Checklist

For design-partner CI reports, capture names and versions for:

- Python version
- OS/platform
- pytest version
- active pytest plugins
- coverage.py usage
- TeamCity usage
- pytest-randomly
- pytest-env
- pytest-sugar
- teamcity-messages
- database driver/client libraries
- Flask, Django, or FastAPI usage
- gevent installed or active in tests

Do not collect secrets. The manifest may include environment variable names,
but should not include environment variable values.

An opt-in source-tree end-to-end smoke covers the failed-test workflow,
including a project path with spaces and explicit pytest plugin loading:

```bash
PYTHONPATH=".venv/lib/python3.12/site-packages" \
  RETRACE_RUN_PYTEST_AGENT_DEV_E2E=1 \
  .venv/bin/python -S -m pytest tests/test_pytest_agent_workflow_e2e.py -q
```

There is also an editable-install packaging smoke:

```bash
RETRACE_RUN_PYTEST_AGENT_EDITABLE_E2E=1 pytest tests/test_pytest_agent_workflow_e2e.py
```

That path is currently blocked by the Meson editable loader before pytest runs.
See `docs/dev/MESON_EDITABLE_BLOCKER.md`.
