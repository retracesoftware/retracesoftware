# Retrace Software

Deterministic record and replay for Python programs, plus replay/debug
tooling. The top-level Python entrypoint in `src/retracesoftware/__main__.py`
records external interactions to a trace and replays them through the same
Python code. The `proxy` layer decides what crosses the internal/external
boundary, the `install` layer patches the runtime, the `stream` layer
serializes trace data, and the Go replay tool handles extraction, indexing,
workspace generation, and DAP workflows.

Keep this file brief and factual. Use it as a table of contents and a list of
hard constraints, not as an encyclopedia.

## Repo Map

- `src/retracesoftware/__main__.py`, `run.py`, `replay/`
  CLI orchestration, recording setup, replay startup, replay binary discovery.
- `src/retracesoftware/install/`
  Runtime patching, import hooks, weakref/thread setup, monitoring, pytest integration.
- `src/retracesoftware/proxy/`
  Record/replay boundary semantics, gates, proxying, message streams.
- `src/retracesoftware/protocol/`
  Semantic replay protocol layered above stream transport.
- `src/retracesoftware/stream/` and `cpp/stream/`
  Trace writing/reading, binding transport, queue transport, thread/process framing.
- `cpp/utils/` and `cpp/cursor/`
  CPython internals, gates, demux, cursor/call-count machinery.
- `src/retracesoftware/dap/` and `go/replay/`
  Replay debugger, DAP control plane, extraction/index tooling.
- `src/retracesoftware/modules/*.toml`
  Module interception config for stdlib and third-party libraries.
- `tests/` and `dockertests/`
  Component tests, replay tests, and scenario/integration tests.

## Mental Model

- "Internal" code is deterministic logic that should re-execute during replay.
- "External" code is nondeterministic library or OS behavior that must be
  intercepted at the boundary and recorded.
- Retrace records boundary crossings, not full process snapshots.
- During record, external calls execute and their results are written to the trace.
- During replay, the same Python code runs, but external calls return values
  from the trace instead of touching the real world.
- The Python replay path currently expects `unframed_binary` recordings.
- If an external call is missed, replay diverges.
- If replay or debugger control-plane I/O is accidentally retraced, the tool
  can interfere with its own replay.
- Replay validates Retrace checksums and exact Python version before running.
- Replay temporarily disables GC to avoid nondeterministic collections changing
  weakref/finalizer timing.
- Thread replay uses stable hierarchical thread ids rather than live OS thread ids.

## High-Risk Invariants

- Replay correctness is more important than convenience.
- Avoid set iteration, `id()`, `hash()`, memory-address ordering, or object
  identity assumptions in replay-sensitive code paths.
- Threading, weakrefs, finalizers, and `fork()` changes are high risk.
- Control-plane I/O for debugging or analysis must bypass retrace gates.
- `cpp/stream` and the trace format are compatibility-sensitive.
- New nondeterministic library behavior usually requires either:
  a module config change in `src/retracesoftware/modules/*.toml`, or
  a boundary/interception change in `install` or `proxy`.
- Do not add unnecessary work to the recording hot path.
- Multi-thread replay depends on preserved message ordering and stable per-thread routing.
- Prefer fixes in the narrowest responsible layer. Avoid mixing `install`,
  `proxy`, and `stream` changes in one diff unless required.
- Packaging is part of correctness here. A change that breaks editable/wheel
  install, package contents, or `python -m retracesoftware install` blocks
  real-world validation before replay behavior is even tested.

## Commands

- Repo / CI build:
  `python -m pip install -e . --no-build-isolation`
- Run Python tests:
  `python -m pytest tests/ -v --tb=short`
- Run Go tests:
  `cd go && go test ./...`
- List docker tests:
  `cd dockertests && python run.py --list`
- Run one docker test:
  `cd dockertests && python run.py <test_name>`
- Enable local auto-activation in the active venv:
  `python -m retracesoftware install`
- Build local Go replay binary if auto-discovery does not work in the
  current checkout layout:
  `cd go && go build -o ../.retrace-replay-bin ./cmd/replay`
  then export `RETRACE_REPLAY_BIN=/absolute/path/to/retracesoftware/.retrace-replay-bin`
  (or `REPLAY_BIN=/absolute/path/to/retracesoftware/.retrace-replay-bin` for
  direct `__main__.py` resolution paths)
- `RETRACE_SKIP_CHECKSUMS=1` exists as a debugging escape hatch for checksum
  mismatches, but do not rely on it for normal validation.
- Debug a recording or replay failure:
  `RETRACE_DEBUG=1 python -m retracesoftware --recording /tmp/test.retrace --verbose --stacktraces -- my_script.py`

After changing `.cpp` files or Meson/native-extension behavior, rebuild the
installed package before running tests. In local wheel-based workflows, rebuild
the wheel, reinstall it into the venv, and rebuild/export the Go replay binary
if needed.

## Working Rules

- If you change `src/retracesoftware/modules/*.toml`, explain which
  nondeterministic call path is being intercepted and add or update tests.
- If you change replay control flow, threading, weakrefs, fork handling, or
  cursor logic, explicitly call out the determinism impact.
- If you touch `cpp/`, `src/retracesoftware/proxy/`, or `src/retracesoftware/dap/`,
  also read the local `AGENTS.md` in that directory if present.
- If you touch `src/retracesoftware/stream/`, `src/retracesoftware/protocol/`,
  or `tests/`, also read the local `AGENTS.md` there if present.
- If you touch `meson.build`, package install lists, or runtime entrypoints,
  run packaging smoke checks in addition to ordinary tests.
- If you touch `dockertests/`, `go/`, or `src/retracesoftware/modules/`,
  also read the local `AGENTS.md` there if present.
- If a task depends on a test-directory-specific manual replay loop, inspect
  the target test directory and existing scripts before suggesting commands.
- For changes touching replay-sensitive boundary logic, threading, weakrefs,
  finalizers, fork behavior, or module interception coverage, consider running
  the repo skill `$determinism-check`.
- When debugging replay failures, prefer locating the first divergence or
  misalignment instead of patching symptoms.

## References

- `docs/LAYERS.md`
- `docs/THREAD_REPLAY.md`
- `docs/STREAM.md`
- `docs/cursors.md`
- `docs/DEBUGGING.md`
- `MODULES_AUDIT.md`
- `LIBRARIES_AUDIT.md`
