# Pytest Agent Workflow Design-Partner Preview

This is an internal preview of Retrace's pytest-agent workflow for review
before sharing it with a design partner.

## What This Preview Is

This is a testing-first workflow for failed pytest runs. `pytest --retrace`
re-execs the pytest session under the Retrace recorder, so the recorded child is
the test run that actually passes or fails. When that recorded session fails,
Retrace keeps the local recording and writes first-failure artifacts that can be
inspected through CLI and MCP entrypoints.

The basic workflow is:

```bash
pytest --retrace
retrace runs
retrace agent-context --latest
retrace inspect --latest
retrace mcp --latest
retrace clean --latest --yes
```

## What This Preview Is Not

- It is not production recording.
- It is not a root-cause engine.
- It is not a replacement for the full VS Code replay workflow.
- It is not guaranteed to expose locals or final exception state for every
  recording yet.
- It is not packaged for the normal editable-install workflow yet because the
  current Meson editable-loader blocker is still open.

## Internal Source-Tree Run Path

For current internal development, avoid the editable-install loader and run the
source-tree test path:

```bash
PYTHONPATH=".venv/lib/python3.12/site-packages" \
  RETRACE_RUN_PYTEST_AGENT_DEV_E2E=1 \
  .venv/bin/python -S -m pytest tests/test_pytest_agent_workflow_e2e.py -q
```

For a local target project while developing from this source tree, explicitly
load the plugin:

```bash
PYTHONPATH="/path/to/retracesoftware/src:/path/to/retracesoftware/.venv/lib/python3.12/site-packages" \
  python -S -m pytest -p retracesoftware.pytest_plugin --retrace tests/test_example.py
```

The intended future path after the editable-install blocker is fixed is:

```bash
pip install -e .
pytest --retrace
```

## Generated Artifacts

Each failed recorded session creates one failed-test artifact directory:

```text
.retrace/runs/<run-id>/
  recording.bin
  manifest.json
  failure.txt
```

`recording.bin` is the local full-session Retrace recording. `manifest.json`
contains pytest, environment, failure, and artifact metadata for the first
observed failure. `failure.txt` is a short plain-text failure summary for
humans and agents. If the recorded session passes, the temporary artifact is
deleted.

## Commands

- `retrace runs` lists recent failed-test artifacts.
- `retrace agent-context --latest` prints a local, evidence-only handoff for
  the newest failed-test artifact.
- `retrace inspect --latest` tries to inspect the newest recording through the
  replay/control path.
- `retrace mcp --latest` starts the MCP server scoped to the newest available
  recording.
- `retrace clean --latest --yes` removes the newest failed-test artifact.
- `retrace clean --all --yes` removes all failed-test artifacts.

## Safety

- Artifacts are local by default.
- There is no automatic upload.
- `recording.bin` may contain runtime data.
- `manifest.json` excludes environment variable values by default.
- Do not share `recording.bin` externally unless that has been explicitly
  agreed.

Safe artifacts to share first:

- `manifest.json`
- `failure.txt`
- `retrace agent-context --latest` output

## Known Limitations

- Editable-install E2E is blocked by the Meson editable-loader issue. See
  `docs/dev/MESON_EDITABLE_BLOCKER.md`.
- `retrace inspect --latest` may return a clear "no inspectable state"
  limitation depending on the replay backend and recording shape.
- `retrace mcp --latest` requires an available, non-placeholder recording.
- v1 reports the first observed failure in the recorded session. It does not
  silently add `--maxfail=1`.
- pytest-xdist is out of scope for v1; correct support requires per-worker
  recordings and manifests.
- The threading/new replay version is not included in this preview.
- Final exception, frame, and locals reliability still depends on separate
  CLI/MCP/DAP hardening work.
- The coverage.py test skips unless coverage is installed in the local test
  environment.

## What We Want From A Design Partner

Ask for:

- pytest config and plugin list
- one failing test or minimal repro
- `manifest.json`
- `failure.txt`
- `retrace agent-context --latest` output
- `recording.bin` only if they are comfortable and sharing has been agreed

## Readiness Checklist

| Item | Status |
| --- | --- |
| Source-tree dev E2E passes | Ready |
| Path with spaces covered | Ready |
| No recursive child artifacts | Ready |
| Failed run creates `manifest.json`, `failure.txt`, and `recording.bin` | Ready |
| Latest resolution works | Ready |
| `agent-context --latest` works | Ready |
| `clean --latest --yes` works | Ready |
| Env var values excluded from manifest, failure text, and context | Ready |
| CI and TeamCity metadata captured | Ready |
| Coverage.py path documented | Ready |
| Editable-install blocker documented | Ready, blocked |
| Known limitations documented | Ready |
