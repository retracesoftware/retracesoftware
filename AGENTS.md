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

## Hard Rules (Non-Negotiable)

1. Before editing any subdirectory listed in Repo Map, read its local
   `AGENTS.md`. Each listed subdirectory has one. If that directory also
   contains a `DESIGN.md` (or any `*_DESIGN.md` / `docs/DESIGN*.md` next to
   it), read it too and treat it as the component's behavior contract, not
   background reading. The proxy layer
   (`src/retracesoftware/proxy/AGENTS.md` + `src/retracesoftware/proxy/DESIGN.md`)
   is the current canonical example, and most other major components are
   expected to grow a `DESIGN.md` over time — always check.
2. Replay never calls live external code. If a fix makes replay touch the real
   filesystem, network, clock, RNG, threading primitives, or any other
   nondeterministic OS/library surface, the fix is wrong.
3. Replay/debugger control-plane I/O must bypass retrace gates. Do not route
   it through the same path as recorded application I/O.
4. There are two `tape.py` files and they are not the same thing:
   - `src/retracesoftware/proxy/tape.py` is small (≈40 lines) and only
     defines the `Tape` / `TapeReader` / `TapeWriter` `Protocol` types.
   - `src/retracesoftware/tape.py` (top level) is the recording I/O
     implementation: `create_tape_writer`, `open_tape_reader`,
     `RawTapeWriter`, checksums, replay-binary discovery. It imports the
     `TapeWriter` Protocol from `proxy/tape.py`.
   Before editing or referencing a `tape.py` symbol, confirm which file it
   lives in. Do not "consolidate" them — the type/implementation split is
   intentional.
5. Prefer fixes in the narrowest responsible layer. Do not modify `install`,
   `proxy`, and `stream` together unless the change genuinely requires all
   three. Cross-layer diffs need an explicit justification.
6. Do not delete or rewrite a high-level abstraction (e.g. proxy types,
   factories, gates) to fix a localized bug without first explaining what
   contract in the relevant `AGENTS.md` or `DESIGN.md` is being violated.
7. Do not introduce backwards-compatibility shims for old trace formats,
   message tags, or APIs. Retrace breaks format/API freely; if a recording
   no longer matches the current code, the recording is regenerated, not
   the code. Compatibility code that already exists in legacy files is
   tolerated only because it has not been deleted yet, not because new
   compatibility code is welcome.
8. Prioritize simplicity above all else. When two correct designs exist,
   pick the smaller one. Do not add abstractions, indirection, or
   "extensibility hooks" without a current, concrete consumer that needs
   them. Deleting code is preferred over generalizing it.

## Repo Map

- `src/retracesoftware/__main__.py`, `run.py`, `replay/`
  CLI orchestration, recording setup, replay startup, replay binary discovery.
  `__main__.py` exposes the `install`/`uninstall` subcommands and the
  record/replay invocation. `pyproject.toml` also installs a `replay` console
  script that points at `retracesoftware.replay:_exec_replay`.
- `src/retracesoftware/install/`
  Runtime patching, import hooks, weakref/thread setup, monitoring, pytest integration.
- `src/retracesoftware/proxy/`
  Record/replay boundary semantics, gates, proxying, message streams.
  Start with `src/retracesoftware/proxy/AGENTS.md` and
  `src/retracesoftware/proxy/DESIGN.md` before editing boundary code here.
- `src/retracesoftware/protocol/`
  Semantic replay protocol layered above stream transport.
- `src/retracesoftware/stream/` and `cpp/stream/`
  Trace writing/reading, binding transport, queue transport, thread/process framing.
- `cpp/utils/`, `cpp/cursor/`, `cpp/common/`, `cpp/functional/`
  CPython internals, gates, demux, cursor/call-count machinery, shared C++
  helpers, and pure-functional helpers used by the native extensions.
- `src/retracesoftware/dap/` and `go/replay/`, `go/cmd/replay/`
  Replay debugger, DAP control plane, extraction/index tooling. The Go binary
  built from `go/cmd/replay/` is what `RETRACE_REPLAY_BIN` / `REPLAY_BIN`
  point at.
- `src/retracesoftware/modules/*.toml`
  Module interception config for stdlib and third-party libraries.
- Top-level Python helpers in `src/retracesoftware/`:
  `tape.py` (top-level recording I/O implementation, distinct from
  `proxy/tape.py` which only holds the `Tape` / `TapeReader` / `TapeWriter`
  Protocol types), `autoenable.py` + `retracesoftware_autoenable.pth`
  (auto-activation when `RETRACE_RECORDING` or `RETRACE_CONFIG` is set
  in the environment — bare `RETRACE=1` does nothing today, despite some
  stale `__main__.py install` help text), `cursor.py`, `control_runtime.py`,
  `search.py`, `exceptions.py`, `run.py`, and the `functional/`, `utils/`,
  `testing/` (incl. `memorytape.py`), and `threadid/` packages. Treat
  these as shared infrastructure used by `install`, `proxy`, `protocol`,
  and `replay`; do not duplicate their helpers in those layers.
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
  (`__main__.py` -> `tape.checksums()`; bypassed only with the
  `RETRACE_SKIP_CHECKSUMS` debug escape hatch.)
- Replay schedules `gc.collect()` deterministically at intercepted safe
  points so GC timing is part of the recording rather than the live
  runtime's whim. The CLI flag is `--gc_collect_multiplier` and the
  hook is `system.wrap_async(gc.collect)` in `proxy/io.py`. There is no
  process-wide `gc.disable()`; the only short-lived `PyGC_Disable` lives
  inside the C++ persister fallback (`cpp/stream/persister.cpp`) around
  one serialize call.
- Thread replay uses stable hierarchical thread-id tuples (built by
  `src/retracesoftware/threadid/ThreadId`, instantiated in
  `src/retracesoftware/__main__.py`) rather than live OS thread ids.

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
- Replay a recording via the installed console script:
  `replay <recording.retrace>`
  (equivalent to `python -m retracesoftware.replay`; uses the same Go replay
  binary discovery as `python -m retracesoftware`).
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

- Each subdirectory listed in Repo Map has its own `AGENTS.md`. Read the
  relevant one before editing that area; do not assume this top-level file
  contains everything you need.
- Whenever a component directory contains a `DESIGN.md` (today: `proxy/`;
  expected to grow to other components), read it before editing that
  component and treat it as the behavior contract for that layer. The same
  rule applies to any `DESIGN.md` placed under `docs/` for a specific
  component.
- If you touch `src/retracesoftware/proxy/`, read both
  `src/retracesoftware/proxy/AGENTS.md` and
  `src/retracesoftware/proxy/DESIGN.md` first. Treat `DESIGN.md` as the proxy
  behavior contract, not optional background reading.
- If you change `src/retracesoftware/modules/*.toml`, explain which
  nondeterministic call path is being intercepted and add or update tests.
- If you change replay control flow, threading, weakrefs, fork handling, or
  cursor logic, explicitly call out the determinism impact.
- If you touch `cpp/`, `src/retracesoftware/dap/`,
  `src/retracesoftware/stream/`, `src/retracesoftware/protocol/`,
  `src/retracesoftware/install/`, `src/retracesoftware/modules/`,
  `tests/`, `dockertests/`, or `go/`, read the local `AGENTS.md` in that
  directory before editing — and any `DESIGN.md` in that directory if one
  exists.
- If you touch `meson.build`, package install lists, or runtime entrypoints,
  run packaging smoke checks in addition to ordinary tests.
- If a task depends on a test-directory-specific manual replay loop, inspect
  the target test directory and existing scripts before suggesting commands.
- For changes touching replay-sensitive boundary logic, threading, weakrefs,
  finalizers, fork behavior, or module interception coverage, consider running
  the repo skill `$determinism-check`.
- When debugging replay failures, prefer locating the first divergence or
  misalignment instead of patching symptoms.
- For proxy-boundary bugs, explain which `src/retracesoftware/proxy/DESIGN.md`
  expectation is being violated before proposing a fix. If you cannot name the
  violated gate, phase, binding, or message-order invariant, inspect the
  design and call flow again before editing code.
- For bugs in any other component that has a `DESIGN.md`, follow the same
  pattern: name the specific contract in that component's `DESIGN.md` that is
  being violated before editing code. If no such contract is named in the
  design, prefer reading more of the design over guessing at a fix.

## References

Architecture / behavior:

- `docs/LAYERS.md`
- `docs/THREAD_REPLAY.md`
- `docs/STREAM.md`
- `docs/cursors.md`
- `docs/DEBUGGING.md`
- `MODULES_AUDIT.md`
- `LIBRARIES_AUDIT.md`

Per-component `DESIGN.md` (behavior contracts — always read before editing
the matching component, and check for new ones when other components grow
their own design docs):

- `src/retracesoftware/proxy/DESIGN.md`

Per-directory AGENTS.md files (read before editing the matching area):

- `cpp/AGENTS.md`
- `go/AGENTS.md`
- `tests/AGENTS.md`
- `dockertests/AGENTS.md`
- `src/retracesoftware/proxy/AGENTS.md`
- `src/retracesoftware/dap/AGENTS.md`
- `src/retracesoftware/install/AGENTS.md`
- `src/retracesoftware/modules/AGENTS.md`
- `src/retracesoftware/protocol/AGENTS.md`
- `src/retracesoftware/stream/AGENTS.md`
