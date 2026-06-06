# Meson Editable Install E2E Blocker

## Summary

The pytest-agent workflow can be validated from the source tree, but the
packaging/editable-install E2E is currently blocked before pytest runs.

The failure is in the Meson editable loader path, not in the pytest-agent
failed-test artifact code. A freshly created project venv can install Retrace
with `pip install -e .`, but importing Retrace later can try to rebuild through
a stale temporary pip build-env path.

## Observed Failure

Running the editable-install smoke:

```bash
RETRACE_RUN_PYTEST_AGENT_EDITABLE_E2E=1 pytest tests/test_pytest_agent_workflow_e2e.py
```

failed before the test project reached pytest execution. The traceback went
through:

```text
_retracesoftware_editable_loader.py
find_spec
_rebuild
```

and ended with a missing temporary Ninja binary similar to:

```text
FileNotFoundError: [Errno 2] No such file or directory:
'/private/var/folders/.../pip-build-env-.../overlay/bin/ninja'
```

## Impact

This blocks the user-shaped editable-install workflow:

```bash
python -m venv .venv
.venv/bin/pip install -e .
.venv/bin/pip install pytest
.venv/bin/pytest --retrace tests/test_example.py
```

It does not block source-tree development validation, where the test runner
loads `src/` directly and avoids the editable loader.

## Current Workaround

Use the source-tree dev-mode E2E:

```bash
PYTHONPATH=".venv/lib/python3.12/site-packages" \
  RETRACE_RUN_PYTEST_AGENT_DEV_E2E=1 \
  .venv/bin/python -S -m pytest tests/test_pytest_agent_workflow_e2e.py -q
```

That path:

- creates a temporary project with a path containing spaces;
- creates a fresh Python venv with no Retrace editable `.pth`;
- loads Retrace from `src/` explicitly;
- loads the pytest plugin explicitly with `-p retracesoftware.pytest_plugin`;
- invokes Retrace CLI paths through module code instead of console scripts.

## Open Question

Nathan/Dan should decide whether this is:

- a packaging configuration issue;
- a Meson editable loader cache issue;
- a missing build dependency persistence issue;
- or expected behavior that requires a different local-dev install path.

The pytest-agent workflow should keep the source-tree E2E as the local
development validation path until the editable-install loader is reliable.
