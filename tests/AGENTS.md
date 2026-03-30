# Test Suite

This directory is not one flat test bucket. It is split by subsystem, and good
changes should usually add or update tests in the narrowest responsible area.

## Layout

- `tests/install/`
  Runtime wiring, import hooks, startup/shutdown, stdlib/external patching.
- `tests/proxy/`
  Boundary behavior, callback semantics, replay divergence rules.
- `tests/stream/`
  Binding, transport, reader/writer parity, backpressure, demux.
- `tests/replay/`
  Replay execution behavior.
- `tests/functional/`, `tests/utils/`
  Lower-level utility behavior.
- `tests/scripts/`
  Subprocess targets and helper scripts used by end-to-end and control/runtime
  tests.
- top-level tests such as `test_stdio_replay.py`, `test_record_replay.py`
  cover cross-layer contracts and CLI/control protocol behavior.

## Failure Classification

Before proposing a fix, classify the failure:

- Product bug
  Real runtime, replay, protocol, or contract regression.
- Environment / sandbox
  Socket bind restrictions, filesystem/network policy, or other local runner
  constraints that do not reflect product behavior.
- Setup / packaging
  Missing build artifacts, wrong interpreter, stale Go binary, wrong editable
  install, or test runner environment mismatch.
- Test drift
  Tests or helpers still assume old CLI flags, old event ordering, or outdated
  helper behavior.

Do not treat all failures as product bugs automatically.

## Current HEAD Verification

When validating a pushed fix, do not assume the local checkout or editable
install is current.

Before summarizing results for a branch tip:

- fetch the remote branch and record the exact commit sha
- verify the worktree `HEAD` matches that sha
- verify the active interpreter is importing the same checkout
  (`python -m pip show retracesoftware` for editable installs)
- only then run tests and summarize failures

Do not report a bug as still live unless it was rerun on the current verified
HEAD.

Label each reported item as one of:

- `Reproduced on current HEAD`
- `Fixed on current HEAD`
- `Not rechecked on current HEAD`

## Current Examples And Recent Hot Spots

These are current examples, not permanent truth. Refresh them when the failure
mix changes.

- Binding/materialization failures such as `ExpectedBindingCreate`
  or runtime errors matching `expected BindingCreate during bind`
  usually belong to stream/protocol/proxy contracts.
- Legacy tests or helpers still using `--raw` instead of current `--format`
  handling are test/CLI drift.
- Extra protocol events ahead of expected events are often contract drift, not
  random flakiness.
- `breakpoint_hit` ordering drift or `set_backstop` mismatches are usually
  control-protocol contract issues, not generic flakiness.
- Sandbox-only socket or bind failures should be separated from unsandboxed
  product failures before recommending architectural changes.
- Shutdown hangs after user code completes are install/run lifecycle bugs.

## Working Rules

- Prefer `tests/helpers.py` for shared record/replay helpers instead of copying
  ad hoc subprocess command construction.
- If a test launches a subprocess or records/replays a helper script, check
  `tests/scripts/` before creating a new ad hoc target.
- When changing CLI or protocol semantics, update the relevant tests in the same
  diff or call out the intentional drift explicitly.
- If a failure is environment-specific, say so clearly and avoid overfitting the
  product code to the local runner.
- Add the narrowest regression test that reproduces the real bug.
- For cross-layer failures, identify the first owning layer before choosing
  which test bucket to extend.

## Sentinel Bundles

Some files have a much wider blast radius than their local tests suggest.

If a diff touches any of these files:

- `src/retracesoftware/proxy/system.py`
- `src/retracesoftware/proxy/_system_specs.py`
- `src/retracesoftware/protocol/replay.py`
- `src/retracesoftware/stream/reader.py`
- `src/retracesoftware/install/__init__.py`
- `src/retracesoftware/install/session.py`
- `src/retracesoftware/proxy/_system_patching.py`
- `src/retracesoftware/proxy/_system_record.py`
- `src/retracesoftware/proxy/_system_replay.py`
- `meson.build`

do not stop at the nearest unit tests. Rerun the adjacent sentinel bundle.

### Proxy Kernel Sentinel Bundle

Run these before saying a proxy-kernel change is safe:

- `tests/proxy/test_patch.py`
- `tests/proxy/test_system_context.py`
- `tests/install/stdlib/test_threaded_select_replay_dispatcher_regression.py`
- `tests/install/external/test_anyio_from_thread_replay_dispatcher_regression.py`
- `tests/install/external/test_starlette_testclient_replay_regression.py`
- `tests/install/external/test_fastapi_testclient_replay_regression.py`
- `tests/test_record_replay.py::test_record_then_replay_fastapi_testclient_request`
- `tests/install/external/test_wsgiref_replay_cleanup_regression.py`

### Install Session / Callback Sentinel Bundle

Run these before saying a callback-binding or install-session change is safe:

- `tests/test_install_session.py`
- `tests/install/stdlib/test_threaded_select_replay_dispatcher_regression.py`
- `tests/install/external/test_anyio_from_thread_replay_dispatcher_regression.py`
- `tests/install/external/test_starlette_testclient_replay_regression.py`
- `tests/install/external/test_fastapi_testclient_replay_regression.py`
- `tests/test_record_replay.py::test_record_then_replay_fastapi_testclient_request`

### Packaging Smoke Bundle

Run these for `meson.build`, install-entrypoint, or package-layout changes:

- fresh editable install in a clean venv
- fresh wheel build in a clean venv
- import smoke:
  `python -c "import retracesoftware.protocol, retracesoftware.testing, retracesoftware.threadid"`
- entrypoint smoke:
  `python -m retracesoftware install`

### Web Replay Ladder

When debugging web replay issues, reduce in this order:

1. child-thread/select replay
2. pure `anyio.from_thread` portal
3. `starlette.testclient.TestClient`
4. FastAPI async endpoint
5. FastAPI sync endpoint
6. plain `wsgiref` single request
7. plain `wsgiref` multi-request

Prefer the smallest rung that still reproduces.

If a change makes one rung green, rerun the highest rung that was failing
before calling the issue fixed. Replay failures can move up or down the ladder
between pushes.

## References

- `tests/helpers.py`
- `tests/scripts/`
- `tests/test_stdio_replay.py`
- `tests/test_record_replay.py`
- `tests/install/stdlib/test_record_shutdown_threadpool_hang_regression.py`
- `tests/stream/test_persister.py`
