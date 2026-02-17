# Retrace — Module Layers

The retrace system is split into six packages, each with a focused
responsibility.  Dependencies flow strictly upward — lower layers never
import higher ones.

```
functional          (no deps)
    ↑
  utils             (+ functional)
    ↑
  stream            (+ functional, utils)
    ↑
  proxy             (+ functional, utils — no stream)
    ↑
  install           (+ proxy, stream, functional, utils)
    ↑
retracesoftware     (+ everything)
```

---

## `functional`

Zero-dependency foundation.  A high-performance functional toolkit
(C++20 with pure-Python fallback): `compose`, `sequence`, `partial`,
`dispatch`, `memoize`, predicates, and more.  Everything else can depend
on it.  It is a general-purpose utility belt, not retrace-specific.

## `utils`

Low-level CPython introspection (C++ extension).  Type-flag manipulation
(`WithFlags`, `WithoutFlags`), deterministic hash patching, `StackFactory`
for call-frame capture, `InterceptDict`, and gate primitives.  Depends
only on `functional`.  Not retrace-specific in principle — it provides
the tools needed to poke at CPython internals safely.

## `stream`

Fast binary serialisation (C++ core, Python wrapper).  Writes and reads
Python objects to a compact wire format with object-identity tracking
(the bind system), thread multiplexing, and custom type hooks.  Depends
on `functional` and `utils`.  This is the I/O layer — it knows *how* to
serialise data, but not *why* you are serialising it.

## `proxy`

The record/replay engine.  Pure logic, no I/O.  `System` defines the
sandbox boundary (patched types, gates, adapters), intercepts every
int→ext and ext→int crossing, and either records or replays via abstract
`Writer`/`Reader` protocols.  Also provides:

- `MemoryWriter` / `MemoryReader` — in-memory backend for testing.
- `StubFactory` — proxy objects for types that are not patched but still
  need to be wrapped (e.g. a list returned by a patched method).
- `proxytype` — dynamic proxy-type construction.

Depends on `functional` and `utils`.  Deliberately has **no dependency
on `stream`** — the proxy layer is backend-agnostic.

## `install`

The wiring layer.  Bridges `proxy` and `stream` with the real Python
runtime:

- Module patching driven by TOML configuration files.
- Import hooks (`install_import_hooks`) and post-load patching
  (`patch_already_loaded`).
- `_thread.start_new_thread` patching for multi-threaded recording.
- `stream_writer()` adapter — maps a `stream.writer` to the
  `proxy.protocol.Writer` interface.
- Preloading, deterministic-hash installation, weakref / GC hooks.
- `TestRunner` and `install_for_pytest` for library-level pytest testing.

Depends on everything below it (`functional`, `utils`, `stream`, `proxy`).

## `retracesoftware`

The user-facing entry point.  Provides:

- CLI: `python -m retracesoftware --recording <path> -- <command>`
- Record/replay orchestration.
- `.pth` auto-enable for transparent activation.

Delegates to `install` for setup and `proxy` + `stream` for the actual
recording.  This is where the whole process — record a run, produce a
trace file, replay it — is assembled.

---

## Design principles

| Principle | Where it shows up |
|---|---|
| **Lower layers are general-purpose** | `functional` and `utils` have no retrace-specific logic. |
| **Proxy is backend-agnostic** | `proxy` defines abstract `Writer`/`Reader` protocols; it never imports `stream`. |
| **Install is the bridge** | Only `install` knows how to wire `proxy` to `stream`, patch modules, and hook imports. |
| **retracesoftware is orchestration** | The top-level package assembles everything but contains minimal logic of its own. |
| **Dependencies flow one way** | No circular imports; each layer can be tested in isolation. |
