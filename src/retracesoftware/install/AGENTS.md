# Install Layer

This directory wires the proxy/stream system into the real Python runtime.
Code here decides when retrace is active, which modules are patched, how thread
and weakref behavior is wrapped, and how runtime hooks are installed and removed.
Many bugs that look like proxy or replay bugs are actually install-layer bugs.

## Current Core Files

- `__init__.py`
  Bootstraps retrace inside a live Python process via `run_with_context`.
- `patcher.py`
  Applies TOML-driven patch specs to modules, types, and functions.
- `importhook.py`
  Hooks module loading so imports run with gates disabled and loaded modules are
  patched afterward.
- `hooks.py`
  Trace/profile hook wrapping and weakref callback wrapping.
- `monitoring.py`
  `sys.monitoring`-based divergence checkpoints on Python 3.12+.
- `startthread.py`
  Wraps `_thread.start_new_thread` so new threads inherit retrace behavior.
- `pathpredicate.py`
  Decides which file/path calls should be retraced versus passthrough.

## Mental Model

- The install layer is the bridge between the abstract record/replay boundary
  and the live Python interpreter.
- `run_with_context()` sets up the runtime, enters the active record/replay
  context, runs the target command, then tears everything back down.
- Thread ids are hierarchical tuples built through thread middleware; changes
  to startup/wrapping can break replay routing even if the program still runs.
- Module patching is driven by `src/retracesoftware/modules/*.toml`.
- Import-time behavior matters: gates are intentionally disabled during heavy
  import machinery and re-enabled around module execution and later patching.
- Hook installation must be reversible. These functions are used both in normal
  runs and in test harnesses.
- `patch_already_loaded()` matters: modules imported before setup still need
  explicit patching.
- Preload timing matters. `preload.txt` exists to import common modules before
  patching/hook installation settles.
- `install_for_pytest()` is the in-process testing path; keep it aligned with
  the main lifecycle rather than letting it drift into a separate runtime model.
- Auto-enable bootstrap is also part of the install story: `autoenable.py` and
  `retracesoftware_autoenable.pth` are what make child processes or fresh
  interpreters start under retrace automatically in some workflows.

## High-Risk Areas

- Import hooks: incorrect wrapping can cause recursion, missed patching, or
  proxying overhead in import paths.
- Thread startup wrapping: wrong behavior here breaks deterministic thread ids
  and replay ordering.
- Weakref/finalizer wrapping: wrong behavior here changes callback timing and
  can cause replay divergence.
- `sys.monitoring`: this must checkpoint user-program behavior without turning
  retrace's own code into noise.
- Path predicates and passthrough rules: wrong filtering can either miss needed
  retracing or proxy too much of the system.
- Already-loaded module patching: ref replacement behavior can be subtle and
  affect modules imported before retrace setup.
- Auto-enable bootstrap drift: changes that desynchronize `.pth` startup,
  environment-driven activation, and the main install lifecycle can break
  child-process activation in ways that look like replay or packaging bugs.
- `atexit` and shutdown behavior: whether cleanup runs inside or outside the
  active context changes what gets recorded.
- `trace_shutdown` is a semantic choice, not just a convenience flag: it
  decides whether exit-time I/O becomes part of the recording.
- Record must not hang after user code has completed. Threadpool/executor
  cleanup, writer drain/close, and atexit ordering are no-break behavior.

## Working Rules

- Prefer fixing interception coverage in `modules/*.toml` or `patcher.py`
  before changing deeper runtime semantics.
- Be explicit about lifecycle: install, patch, run, uninstall.
- `run_with_context()` also owns thread-id initialization, hook installation,
  module patching order, and teardown order. Treat it as a lifecycle coordinator.
- Any hook that can recurse into Python execution must be reviewed for gate
  bypass and re-entrancy behavior.
- If you change thread, weakref, import, or monitoring behavior, explain the
  determinism impact and update focused tests.
- If you change shutdown ordering, explicitly reason about:
  non-daemon thread waiting, threadpool/executor cleanup, queue drain/close,
  and whether atexit hooks run inside or outside the active context.
- Do not casually proxy import machinery or retrace-internal monitoring events;
  the current design disables or filters these for a reason.
- Keep fixes narrow. Install-layer changes can affect the entire runtime.
- Path predicates are intentionally asymmetric: file descriptors are always
  retraced, while path-like arguments are filtered by regex patterns.

## Build And Test

- Rebuild/reinstall the package after changing install behavior if the active
  environment depends on installed code.
- Python tests:
  `python -m pytest tests/install tests/proxy tests/test_record_replay.py -v --tb=short`
- Full Python suite:
  `python -m pytest tests/ -v --tb=short`
- Use `RETRACE_DEBUG=1` and `--verbose --stacktraces` when diagnosing lifecycle
  or divergence problems.

## References

- `src/retracesoftware/install/__init__.py`
- `src/retracesoftware/install/patcher.py`
- `src/retracesoftware/install/importhook.py`
- `src/retracesoftware/install/hooks.py`
- `src/retracesoftware/install/monitoring.py`
- `src/retracesoftware/install/startthread.py`
- `src/retracesoftware/install/pathpredicate.py`
- `src/retracesoftware/autoenable.py`
- `src/retracesoftware/retracesoftware_autoenable.pth`
- `src/retracesoftware/modules/*.toml`
- `docs/DEBUGGING.md`
