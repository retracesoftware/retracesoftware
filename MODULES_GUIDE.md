# Adding Modules to modules.toml

A guide for determining whether a Python module needs an entry in `modules.toml`, and how to write one.

---

## Table of Contents

- [Core Concept](#core-concept)
- [Decision Framework](#decision-framework)
- [Inspection Technique](#inspection-technique)
- [Available Directives](#available-directives)
- [Step-by-Step Process](#step-by-step-process)
- [Patterns](#patterns)
- [Edge Cases](#edge-cases)
- [Checklist](#checklist)

---

## Core Concept

For record/replay to work, **every function call that could return a different value on re-execution must be intercepted**. During recording, the proxy captures inputs and outputs. During replay, the proxy returns the recorded output without executing the real function.

A module needs an entry in `modules.toml` when it contains **non-deterministic behavior at the C level** — meaning it makes syscalls, uses its own RNG, reads system state, or performs I/O directly in C, bypassing already-proxied Python modules.

A module does **not** need an entry if:
- It is pure Python and delegates all non-determinism to modules we already proxy (`posix`, `_socket`, `_ssl`, `time`, `_random`, etc.)
- It is deterministic — same inputs always produce same outputs (e.g., `_json`, `_hashlib`, `_struct`)

---

## Decision Framework

For any module, ask these questions in order:

### 1. Is it pure Python?

If yes, trace its I/O and non-determinism to the underlying C modules. If all paths flow through already-proxied modules, **no entry needed**.

Examples of pure Python modules that are covered transitively:
- `requests` → `urllib3` → `_socket` / `_ssl` (proxied)
- `subprocess` → `_posixsubprocess.fork_exec` (proxied)
- `tempfile` → `posix.*` + `_random.Random` (proxied)

### 2. Does it have C extensions?

If yes, determine what the C extensions do:

- **Parsing/serialization only** (no syscalls) → No entry needed. Examples: `hiredis`, `ujson`, `orjson`, `bson._cbson`, `aiohttp._http_parser`
- **Performance wrappers that still use Python I/O** → No entry needed. The Cython/C code calls Python socket/file objects. Examples: `asyncpg`, `oracledb` thin mode, `aiohttp._http_writer`
- **Wraps a C library that does its own I/O** → **Entry needed.** The C library makes direct syscalls (`socket`, `connect`, `read`, `write`, `open`, etc.) bypassing Python entirely. Examples: `psycopg2._psycopg` (libpq), `_mysql` (libmysqlclient), `grpc._cython.cygrpc` (gRPC C core)
- **Has its own RNG or entropy source** → **Entry needed.** The C code calls `getrandom(2)` or reads `/dev/urandom` directly, bypassing `posix.urandom`. Examples: `torch`, `_uuid`

### 3. Is it deterministic?

If the module performs only pure computation with no side effects and no dependency on external state, **no entry needed**.

Examples: `_hashlib`, `_zlib`, `_struct`, `_pickle`, `_decimal`, `_heapq`, `_bisect`

### 4. Quick reference table

| C extension does... | Needs entry? |
|---|---|
| Direct socket/file syscalls | Yes — `proxy` |
| Own RNG / entropy gathering | Yes — `proxy` |
| Reads system databases (users, groups, locale) | Yes — `proxy` |
| Queries system state (time, PID, hostname) | Yes — `proxy` |
| Pure parsing/serialization | No |
| Math/computation only | No |
| Calls Python socket/file objects | No (covered transitively) |
| Wraps Python objects for performance | No |

---

## Inspection Technique

When adding a new module, first enumerate its exports to understand what needs proxying.

### List all types and functions in a C extension module

```python
import importlib
module = importlib.import_module("grpc._cython.cygrpc")

names = [n for n in dir(module) if not n.startswith('_')]

for name in sorted(names):
    obj = getattr(module, name)
    if isinstance(obj, type):
        methods = [m for m in dir(obj)
                   if not m.startswith('__') and callable(getattr(obj, m, None))]
        print(f"TYPE  {name}: {methods}")
    elif callable(obj):
        print(f"FUNC  {name}")
    else:
        print(f"OTHER {name} ({type(obj).__name__})")
```

### Check if a pure Python module delegates I/O to proxied modules

```python
import inspect, socket, ssl

module = importlib.import_module("some_library.connection")

# Look for socket usage
source = inspect.getsource(module)
for keyword in ['socket', '_socket', 'ssl', 'os.', 'posix', 'open(']:
    if keyword in source:
        print(f"Found: {keyword}")
```

### Verify a C extension's I/O path

For compiled extensions where you can't read source, check with `strace`/`dtrace`:

```bash
# Linux
strace -e trace=network,file -f python3 -c "import module; module.do_something()" 2>&1 | grep -E 'socket|connect|open|read|write'

# macOS
sudo dtruss -f python3 -c "import module; module.do_something()" 2>&1 | grep -E 'socket|connect|open|read|write'
```

If you see syscalls coming from the C extension's `.so` (not from `_socket.cpython-*.so` or `posix`), the extension is doing direct I/O.

---

## Available Directives

Each directive in `modules.toml` serves a specific purpose. A module entry can use multiple directives.

### `proxy`

**When to use:** For functions and types that are non-deterministic — they do I/O, return time/random values, query system state, etc.

**What it does:** Wraps the function/type with a proxy object via `system(obj)`. During recording, calls are intercepted and inputs/outputs are captured. During replay, recorded outputs are returned without executing the real function.

**For functions:** The function call is intercepted.
**For types:** The type constructor is intercepted, and all method calls on instances are intercepted.

```toml
[_socket]
proxy = ["socket", "getaddrinfo", "gethostname"]

# socket      → type, so socket() construction + all methods (recv, send, etc.) captured
# getaddrinfo → function, so each call captured
```

### `disable`

**When to use:** For functions that must be prevented from running during record/replay because they interfere with the system (debugger hooks, exception hooks, encoding lookups).

**What it does:** Wraps the function with `system.disable_for()`, which prevents it from executing.

```toml
[sys]
disable = ["excepthook"]

[bdb.type_attributes]
Bdb.disable = ["trace_dispatch"]
```

### `immutable`

**When to use:** For types whose instances are value-like and should be treated as immutable data (not proxied objects). The proxy system needs to know these types so it can serialize/deserialize their instances directly rather than tracking them as mutable proxy objects.

**What it does:** Adds the type to `system.immutable_types`.

```toml
[builtins]
immutable = ["int", "float", "str", "bytes", "bool"]

[_datetime]
immutable = ["datetime", "tzinfo", "timezone"]
```

### `patch_hash`

**When to use:** For types whose instances are used as set elements or dict keys and whose default `__hash__` is based on `id()` (memory address). Since memory addresses change between runs, set iteration order becomes non-deterministic.

**What it does:** Replaces `__hash__` with a deterministic counter-based hash. This ensures stable set iteration order for record/replay.

**Note:** Only needed for types that use the default `id()`-based hash. Types that define their own `__hash__` (like `str`, `int`, `tuple`) are already deterministic.

```toml
[builtins]
patch_hash = ["object"]

[types]
patch_hash = ["FunctionType"]
```

### `bind`

**When to use:** For enum types or singleton objects that should be registered with the proxy system by identity, so the same object is used during replay.

**What it does:** Calls `system.bind(obj)`. For `enum.Enum` subclasses, binds each member individually.

```toml
[ssl]
bind = ["Options"]
```

### `wrap`

**When to use:** When a function needs a custom wrapper that transforms arguments or behavior beyond what `proxy` provides. Used for special cases where you need to inject environment variables, modify arguments, etc.

**What it does:** Replaces the function with a custom wrapper loaded from the specified module path. The wrapper receives the original function as its argument and returns a new callable.

```toml
[posix.wrap]
posix_spawn = "retracesoftware.install.edgecases.posix_spawn"

[_posixsubprocess.wrap]
fork_exec = "retracesoftware.install.edgecases.fork_exec"
```

The wrapper function pattern (from `edgecases.py`):

```python
def posix_spawn(target):
    """Wraps posix_spawn to inject retrace env vars into child processes."""
    def transform(env):
        return {**env, **retrace_env()}

    return transform_argument(
        target=target, position=2, name='env',
        transform=transform, default=os.environ)
```

### `patch_class`

**When to use:** When specific methods on a proxied type need custom replacement implementations. Commonly used for buffer-writing methods (`recv_into`, `readinto`) that can't be proxied directly because they write into pre-allocated buffers passed by the caller.

**What it does:** Replaces specific methods on a class with custom implementations loaded from a module path.

```toml
[_socket.patch_class.socket]
recvfrom_into = "retracesoftware.install.edgecases.recvfrom_into"
recv_into = "retracesoftware.install.edgecases.recv_into"

[_ssl.patch_class._SSLSocket]
read = "retracesoftware.install.edgecases.read"
```

### `type_attributes`

**When to use:** When you need to apply a directive (like `disable` or `proxy`) to specific methods of a type, rather than to the type itself.

**What it does:** Applies the specified directive to the named attributes of the type.

```toml
[_datetime.type_attributes]
datetime.proxy = ["now", "utcnow", "today"]
# Only proxies the now/utcnow/today class methods on datetime,
# not the entire datetime type (which is immutable)
```

### `default`

**When to use:** As a fallback strategy for modules where you want to attempt proxying any attribute that isn't explicitly listed. Currently only `"try_proxy"` is supported.

**Note:** This is partially implemented — it will trigger a `sigtrap` if encountered by the patcher for unknown directives. Only use if the patcher has been updated to handle it.

```toml
[_sqlite3]
proxy = ["Blob", "Connection", "Cursor", "adapt", "connect"]
default = "try_proxy"
```

---

## Step-by-Step Process

### Adding a new stdlib C extension module

1. **Enumerate exports** using the inspection technique above.
2. **Classify each export** as:
   - Non-deterministic function → `proxy`
   - Non-deterministic type (I/O-bearing) → `proxy`
   - Value/data type → `immutable`
   - Exception type → `immutable`
   - Constant → skip (deterministic)
   - Deterministic function → skip
3. **Check for buffer APIs** — methods like `recv_into`, `readinto` that write into caller-provided buffers need `patch_class` with a custom wrapper in `edgecases.py`.
4. **Check for subprocess interaction** — if the module spawns child processes, you may need `wrap` to inject retrace environment variables.
5. **Write the TOML entry** and add it to `modules.toml`.

### Adding a third-party library

1. **Identify the C extension module** — the `.so`/`.pyd` file that contains the C code. This is what you proxy, not the pure Python wrapper modules.
2. **Enumerate the C extension's exports** using the inspection technique.
3. **Identify the I/O boundary** — which types/functions are the entry points for I/O operations? These are typically:
   - Connection/channel types (e.g., `Channel`, `Connection`)
   - Result/cursor types (e.g., `Cursor`, `Result`)
   - Factory functions (e.g., `connect()`)
4. **Include all types in the call chain** — if a proxied type's method returns another type that also has I/O methods, proxy that type too. Example: `psycopg2._psycopg` proxies both `connection` and `cursor` because `connection.cursor()` returns a `cursor` with its own I/O methods.
5. **Write the TOML entry** with the fully qualified module path.

---

## Patterns

### Pattern 1: Simple C extension with a few functions

For modules that are just a collection of C functions (no complex types).

```toml
[pwd]
proxy = ["getpwnam", "getpwuid", "getpwall"]
```

### Pattern 2: C extension with I/O-bearing types

For modules where the main interaction is through type instances. Proxy the types so all method calls are captured.

```toml
[_socket]
immutable = ["error", "herror", "gaierror", "timeout"]
proxy = ["socket", "close", "dup", "getaddrinfo", ...]
```

### Pattern 3: Type where only some methods are non-deterministic

When a type is mostly immutable/data but has a few non-deterministic class methods, use `type_attributes` to proxy only those methods while marking the type as `immutable`.

```toml
[_datetime]
immutable = ["datetime", "tzinfo", "timezone"]

[_datetime.type_attributes]
datetime.proxy = ["now", "utcnow", "today"]
```

### Pattern 4: Third-party library wrapping a C library

The C library does all I/O internally. Proxy at the Python/C boundary — the types and functions that the Python code calls into.

```toml
["psycopg2._psycopg"]
proxy = ["connect", "cursor", "connection"]
```

### Pattern 5: Module with buffer-writing methods

Some C methods write into pre-allocated buffers (e.g., `socket.recv_into(buffer)`). These can't be proxied directly because the proxy needs to capture the data, but the buffer is provided by the caller. Solution: replace the method with a wrapper that calls the non-buffer version and copies data.

```toml
[_socket]
proxy = ["socket", ...]

[_socket.patch_class.socket]
recvfrom_into = "retracesoftware.install.edgecases.recvfrom_into"
recv_into = "retracesoftware.install.edgecases.recv_into"
```

The wrapper in `edgecases.py`:

```python
def recv_into(target):
    @functools.wraps(target)
    def wrapper(self, buffer, nbytes=0, flags=0):
        data = self.recv(len(buffer) if nbytes == 0 else nbytes, flags)
        buffer[0:len(data)] = data
        return len(data)
    return wrapper
```

### Pattern 6: Module with subprocess spawning

When a module spawns child processes, use `wrap` to inject retrace environment variables so the child process is also recorded.

```toml
[_posixsubprocess]
proxy = ["fork_exec"]

[_posixsubprocess.wrap]
fork_exec = "retracesoftware.install.edgecases.fork_exec"
```

---

## Edge Cases

### Module names with dots

For modules with dots in their names (like third-party packages), quote the TOML key:

```toml
["psycopg2._psycopg"]
proxy = ["connect", "cursor", "connection"]

["grpc._cython.cygrpc"]
proxy = ["Channel", "Server", ...]
```

### Sub-key sections with dots

For `type_attributes`, `patch_class`, and `wrap` on dotted module names, the dot-separated path must be correctly structured:

```toml
["_pydevd_bundle.pydevd_cython".type_attributes]
ThreadTracer.disable = ["__call__"]
```

### Platform-specific modules

Some modules only exist on certain platforms. The patcher only patches names that exist in the module's namespace (`if name in namespace`), so listing a function that doesn't exist on the current platform is safe — it will simply be skipped.

### Optional dependencies

Third-party library entries are safe to include even if the library isn't installed. The patcher only runs when the module is actually imported (`patch_imported_module`). If the module is never imported, the entry is ignored.

### Existing entries that don't match the module

If a TOML section key doesn't match any importable module name (e.g., the `[fnctl]` typo — should be `[fcntl]`), the entry will never trigger. The patcher matches by `__name__` in the module's namespace.

---

## Checklist

Before submitting a new `modules.toml` entry, verify:

- [ ] **Module name is spelled correctly** (the TOML section key must match `module.__name__`)
- [ ] **All proxied names exist in the module** (run `dir(module)` to confirm)
- [ ] **I/O-bearing types are proxied**, not just factory functions
- [ ] **Return types of proxied methods are also proxied** if they have their own I/O methods
- [ ] **Buffer-writing methods** have `patch_class` wrappers if needed
- [ ] **Value/data types** are marked `immutable` if appropriate
- [ ] **Exception types** are marked `immutable` if they exist in the module
- [ ] **Comments explain the rationale** — why this module needs proxying, what C library it wraps, what I/O it does
