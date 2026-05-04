# VS Code Flask TestClient Debug Repro

This fixture is a deterministic Flask `test_client()` app for exercising a heavier VS Code debugger path without starting a live server.

It covers:

- Flask route handlers.
- Flask request/session context.
- Jinja template rendering.
- JSON responses.
- Form parsing.
- A service/repository split.

## Record The Fixture

From this directory:

```bash
PYTHON_BIN=python3.12 ./record.sh
```

By default, `record.sh` installs `retracesoftware==0.2.3` and `flask` into `.venv`.
To test a local checkout instead, run for example:

```bash
RETRACE_INSTALL_TARGET=../.. RETRACE_PIP_ARGS="--no-build-isolation" PYTHON_BIN=python3.12 ./record.sh
```

Expected program output:

```text
INDEX 200 2
API 200 in-stock
MISSING 404 not-found
CHECKOUT 200 12
```

The script writes:

- `flask-testclient.retrace`
- `flask-testclient.d/`
- `flask-testclient.code-workspace`

These generated files are intentionally ignored by git.

## Manual VS Code Repro

Open the generated workspace:

```bash
code flask-testclient.code-workspace
```

Then use `Retrace: Open Recording` and select `flask-testclient.retrace`.

Set one breakpoint only:

- `app/web.py:17`

That line is:

```python
products = service.visible_products(min_price=min_price)
```

At the time this repro was added, the breakpoint scan failed with:

```text
Checkpoint difference: {'function': wrapped_function:posix.stat -> builtin_function_or_method:posix.stat, 'args': ('.../.venv/lib/python3.12/site-packages'), 'kwargs': {}} was expecting {'function': wrapped_function:posix.listdir -> builtin_function_or_method:posix.listdir, 'args': ('.../vscode_flask_testclient_debug_repro'), 'kwargs': {}}
breakpoint scan[1]: complete, 0 hits found
handleContinue: ok=false hitMsgIdx=0
```

The recording itself succeeds. The failure is specific to VS Code breakpoint scanning replay.
