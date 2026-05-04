# Live Flask Replay Repro

This fixture is a deterministic live Flask server repro. Unlike the Flask TestClient fixture, this starts a real Werkzeug server in a background thread and sends real HTTP requests to localhost with `requests`.

It covers:

- Flask route handlers.
- Werkzeug `make_server`.
- A background server thread.
- Real local socket I/O.
- `requests` client calls.
- JSON request/response handling.
- Controlled server shutdown.

## Record The Fixture

From this directory:

```bash
PYTHON_BIN=python3.12 ./record.sh
```

By default, `record.sh` installs `retracesoftware==0.2.3`, `flask`, and `requests` into `.venv`.
To test a local checkout instead, run for example:

```bash
RETRACE_INSTALL_TARGET=../.. RETRACE_PIP_ARGS="--no-build-isolation" PYTHON_BIN=python3.12 ./record.sh
```

Expected program output during plain run and record, alongside Werkzeug request logs:

```text
HEALTH 200 True
PRODUCTS 200 2 A100
QUOTE 200 12
MISSING 404 not-found
```

The script writes `flask-live.retrace`.

## Manual Replay Repro

Extract the PidFile:

```bash
./flask-live.retrace --extract
```

Then replay the extracted PidFile:

```bash
./flask-live.d/<pid>.bin
```

If the checkout path contains spaces, generated shebang execution may fail before
the replay tool starts. In that case, run the replay binary directly:

```bash
.venv/bin/replay --recording flask-live.retrace --extract
.venv/bin/replay flask-live.d/<pid>.bin
```

At the time this repro was added, recording succeeded but PidFile replay failed
with a checkpoint divergence. One observed failure was:

```text
Checkpoint difference: {'function': wrapped_function:posix.stat -> builtin_function_or_method:posix.stat, 'args': ('.../.venv/lib/python3.12/site-packages'), 'kwargs': {}} was expecting {'function': wrapped_function:posix.listdir -> builtin_function_or_method:posix.listdir, 'args': ('.../flask_live_replay_repro'), 'kwargs': {}}
replay: replay exited: exit status 1
```

This is the same divergence shape as the Flask TestClient VS Code breakpoint-scan failure, but here it occurs in terminal replay without VS Code.
