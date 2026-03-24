# Native C++ Layer

This directory contains the native extension code for Retrace. Mistakes here
can cause segfaults, bus errors, silent memory corruption, replay drift, GIL
deadlocks, or performance regressions that are much harder to diagnose than
normal Python failures.

## Current Subsystems

- `cpp/stream/`
  Trace transport, queueing, writer/reader pipeline, wire-format-sensitive code.
- `cpp/utils/`
  CPython internals, gates, wrappers, demux, thread state, hash patching,
  traceback stripping, and low-level helpers used across the runtime.
- `cpp/cursor/`
  Cursor and call-count tracking for replay/debugger navigation.
- `cpp/functional/`
  Lower-level utility code and native functional helpers. Usually less risky
  than `stream` or `utils`, but still native code.

## Mental Model

- Native code here is part of the product, not just an optimization layer.
- `cpp/stream` is both correctness-sensitive and performance-sensitive.
- `cpp/utils` is deeply tied to CPython internals and runtime invariants.
- `cpp/cursor` is debugger/cursor machinery and may depend on Python version
  details and `sys.monitoring` behavior.
- A passing Python test is not enough evidence that a native refactor is safe.
- The recording path uses background writer/return threads and SPSC queues to
  keep the main thread fast. Preserve that design pressure when editing code.
- Some paths deliberately reacquire the GIL with `retracesoftware::GILGuard`
  at the Python boundary. Preserve that boundary rather than letting Python
  object work leak into queueing or transport loops.

## Reference Counting

- Know whether each `PyObject*` is borrowed, owned, immortal, or a weakref token.
- `PyObject_Call*`, `PyObject_GetAttr*`, `PyFrame_GetCode`, and similar APIs
  typically return new references unless documented otherwise.
- Clean up all owned references on every error path before returning `NULL`.
- `Py_DECREF` can trigger `tp_dealloc`, weakref callbacks, GC side effects, or
  other Python execution. Be careful when decrefing under locks or during
  sensitive state transitions.
- Do not casually change ownership boundaries in queue, writer, or binding code.

## GIL Discipline

- Assume the GIL is required for `PyObject*` access unless an existing code path
  intentionally proves a narrower safe case. Preserve those exceptions carefully.
- Blocking I/O, queue waits, and native-only spin/wait loops should stay GIL-free
  when the current design expects them to be.
- Do not introduce Python object access into GIL-free hot paths such as queue
  waiting, background writer loops, or return-thread loops.
- If a change moves work between GIL-held and GIL-free sections, explain why.

## Version-Sensitive Behavior

- This repo currently targets CPython 3.11 and 3.12. Native code already has
  `PY_VERSION_HEX` branches for version-specific behavior.
- Examples exist in `cpp/utils/module.cpp` and `cpp/stream/queue.h`; do not
  assume frame layout, immortality rules, or internal structs are stable.
- When touching CPython internal APIs or frame/cursor logic, verify the change
  against the target Python versions explicitly.
- If a change crosses one of the version branches, review both sides rather than
  only editing the branch for the interpreter you are currently running.

## High-Risk Areas

- `cpp/stream/queue.*`
  SPSC queue transport, inflight accounting, background threads, shutdown/drain,
  and thread-switch injection.
- `cpp/stream/queueentry.h`
  Internal tagged-pointer/command protocol. Small changes here can corrupt transport semantics.
- `cpp/stream/queue.cpp`
  The live queue implementation. Be careful with wakeups, inflight accounting,
  shutdown, and where the GIL is reacquired.
- `cpp/stream/objectwriter.cpp`, `objectstream.cpp`, `persister.cpp`
  Serialization, replay reads, target callbacks, and trace transport.
- `cpp/utils/module.cpp`
  Core helpers using CPython internals and interpreter frame details.
- `cpp/utils/demux.cpp`, `threadstate.cpp`, `threadcontext.cpp`, `frameeval.cpp`
  Replay ordering, thread behavior, and interpreter/runtime hooks.
- `cpp/utils/striptraceback.cpp`
  Exception traceback lifetime affects object lifetimes and replay correctness.
- `cpp/cursor/*`
  Cursor/call-count behavior used by replay debugging.

## Working Rules

- Keep native fixes narrow. Avoid mixing refactors, behavior changes, and
  performance changes in one diff unless required.
- If you change transport or serialization behavior in `cpp/stream`, call out
  wire-format or compatibility impact explicitly.
- If you change thread ordering, queue wakeups, or background-thread behavior,
  call out the replay/determinism impact explicitly.
- Do not "simplify" inflight accounting, thread-switch injection, or error
  propagation paths in the queue without tracing the producer/consumer protocol.
- If you change CPython-internal assumptions, cite the exact runtime behavior or
  version branch you are relying on.
- Prefer preserving proven invariants over "cleaning up" native code that looks
  awkward but encodes a hard-won correctness constraint.
- If you add main-thread work in `cpp/stream`, justify the overhead explicitly.

## Build And Test

- Rebuild the installed package after native changes.
- Repo/CI path:
  `python -m pip install -e . --no-build-isolation`
- Python tests:
  `python -m pytest tests/ -v --tb=short`
- Go tests when replay/debugger behavior is involved:
  `cd go && go test ./...`
- For local wheel-based workflows, rebuild and reinstall the wheel into the venv
  before testing.
- Use `RETRACE_DEBUG=1` when you need native assertions and debug builds.

## References

- `meson.build`
- `cpp/stream/queue.h`
- `cpp/stream/objectwriter.cpp`
- `cpp/stream/objectstream.cpp`
- `cpp/stream/persister.cpp`
- `cpp/utils/module.cpp`
- `cpp/utils/demux.cpp`
- `cpp/cursor/module.cpp`
- `docs/STREAM.md`
- `docs/cursors.md`
- `docs/DEBUGGING.md`
