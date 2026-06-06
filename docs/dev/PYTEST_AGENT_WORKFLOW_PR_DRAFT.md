# Pytest Agent Workflow PR Draft

## Title

PYTEST: add failed-test recording workflow for agent inspection

## Summary

This adds an opt-in pytest workflow for failed-test debugging with Retrace:

- Adds pytest plugin support via `--retrace`.
- Captures failed-test artifacts under `.retrace/runs/<run-id>/`.
- Writes `recording.bin`, `manifest.json`, and `failure.txt`.
- Adds failed-test manifest and failure context metadata.
- Adds `retrace runs`.
- Adds `retrace inspect --latest`.
- Adds `retrace agent-context --latest`.
- Adds `retrace mcp --latest`.
- Adds `retrace clean`.
- Adds local-first safety and retention basics.
- Adds CI, TeamCity, coverage.py, and pytest plugin metadata.
- Adds source-tree dev-mode E2E coverage.
- Documents the current Meson editable-install blocker.

The intent is to give agents and humans a local, deterministic handoff after a
pytest failure without changing core record/replay internals.

## Testing

Focused suite:

```bash
PYTHONPATH="src:.venv/lib/python3.12/site-packages" \
  .venv/bin/python -S -m pytest \
  tests/test_pytest_runs.py \
  tests/test_cli_pytest.py \
  tests/test_pytest_plugin.py \
  tests/test_pytest_agent_workflow_e2e.py \
  tests/test_pytest_docs.py
```

Result:

```text
41 passed, 3 skipped
```

Source-tree dev E2E:

```bash
PYTHONPATH=".venv/lib/python3.12/site-packages" \
  RETRACE_RUN_PYTEST_AGENT_DEV_E2E=1 \
  .venv/bin/python -S -m pytest tests/test_pytest_agent_workflow_e2e.py -q
```

Result:

```text
1 passed, 1 skipped
```

Skipped tests:

- Editable-install E2E is skipped by default and remains blocked by the Meson
  editable-loader issue.
- Coverage.py invocation test skips when coverage is not installed locally.

## Known Limitations

- Recording inspection still depends on the replay/control backend exposing an
  inspectable stopped state.
- Editable install is blocked by the Meson editable-loader issue and is
  documented separately.
- No core replay, DAP, trace format, threading, cursor/frame reconstruction, or
  exception tracking changes are included.
- This is not a root-cause engine and does not promise automatic fixing.

## Issue Coverage

Implemented or substantially addressed:

- #24 PYTEST: record failed tests only and print next-step commands
- #25 PYTEST: create failed-test manifest next to each recording
- #26 CLI: add `--latest` support for failed-test recordings
- #27 CLI: add agent context command for latest failed test
- #28 MCP: make latest failed-test recording easy to launch from pytest workflow
- #30 SAFETY: add local-first recording messaging and retention policy
- #31 PYTEST: add minimal configuration surface
- #34 CI: support pytest runs launched through coverage.py and TeamCity

Partially addressed:

- #32 DOCS: write pytest plugin quickstart for design partners
- #33 DISCOVERY: prepare design-partner pytest compatibility checklist
