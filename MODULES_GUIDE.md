# Module Configuration Guide

A guide for determining whether a Python module needs a patching configuration, and how to write one.

---

## Table of Contents

- [File Structure](#file-structure)
- [Core Concept](#core-concept)
- [Decision Framework](#decision-framework)
- [Inspection Technique](#inspection-technique)
- [Available Directives](#available-directives)
- [Step-by-Step Process](#step-by-step-process)
- [Patterns](#patterns)
- [Buffer Protocol and Memoryview Types](#buffer-protocol-and-memoryview-types)
- [Edge Cases](#edge-cases)
- [Checklist](#checklist)

---

## File Structure

Module configs live in `src/retracesoftware/modules/*.toml` and are loaded by `ModuleConfigResolver` (defined in `modules/__init__.py`).

### Directory layout

```
src/retracesoftware/modules/
  __init__.py                       # ModuleConfigResolver class
  stdlib.toml                       # Grouped: posix, _socket, _ssl, time, builtins, ...
  debuggers.toml                    # Grouped: bdb, pydevd, _pydevd_bundle
  _sqlite3.toml                     # Single-module
  psycopg2._psycopg.toml            # Single-module
  grpc._cython.cygrpc.toml          # Single-module with versioning
```

### Two file formats

**Grouped files** — section headers are Python module names. Used for grouping related modules (e.g., all stdlib modules in one file):

```toml
# stdlib.toml
[posix]
proxy = ["read", "write", "open", ...]

[_socket]
proxy = ["socket", "getaddrinfo", ...]
immutable = ["error", "herror", "gaierror", "timeout"]
```

**Single-module files** — filename is the module name. Root table holds the base config. Optional `package` key enables version-aware loading. Optional version sections add config for specific library versions:

```toml
# grpc._cython.cygrpc.toml
package = "grpcio"

proxy = ["Channel", "Server", "CompletionQueue"]
immutable = ["BaseError", "AbortError"]

["1.60"]
proxy = ["AioChannel", "AioServer"]
bind = ["AsyncIOEngine"]
```

- Root-level directives (`proxy`, `immutable`, etc.) — base config, always applied
- `package` — optional PyPI package name for version lookup via `importlib.metadata`. If set and the package is not installed, the file is silently skipped
- `["1.60"]` — version section, applied additively when the installed version is >= 1.60. **Must be quoted** — TOML treats unquoted `[1.60]` as a dotted key path, not a literal key
- Version sections are evaluated in ascending order; their lists are **appended** to the base

### Loading order (`RETRACE_MODULES_PATH`)

The resolver searches two locations. First match wins per module name:

1. **User directory** — `RETRACE_MODULES_PATH` env var, defaults to `.retrace/modules/`
2. **Built-in directory** — `retracesoftware/modules/*.toml` (shipped with the package)

To override a built-in config, create a `.toml` file with the same module name in your user directory. The user file completely replaces the built-in config for that module.

If the user directory doesn't exist, it is silently skipped.

### Format auto-detection

The resolver distinguishes the two formats automatically:
- If any root-level value is not a dict (e.g., a list or string) → **single-module file** (filename = module name)
- If all root-level values are dicts → **grouped file** (section headers = module names)

---

## Core Concept

For record/replay to work, **every function call that could return a different value on re-execution must be intercepted**. During recording, the proxy captures inputs and outputs. During replay, the proxy returns the recorded output without executing the real function.

A module needs a config entry when it contains **non-deterministic behavior at the C level** — meaning it makes syscalls, uses its own RNG, reads system state, or performs I/O directly in C, bypassing already-proxied Python modules.

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

Each directive serves a specific purpose. A module entry can use multiple directives.

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

**Always prefer `immutable` over `proxy` where possible.** Proxying a type has overhead — every method call on every instance is intercepted, recorded, and replayed. If a type is just carrying data and has no I/O-bearing methods, marking it `immutable` is cheaper and more correct. Reserve `proxy` for types that have methods which actually perform non-deterministic operations (I/O, system calls, etc.).

**What it does:** Adds the type to `system.immutable_types`. Instances are serialized as values in the recording stream. For primitive types (`int`, `str`, `bytes`, etc.) the serializer uses built-in encoding. For all other types, **serialization delegates to Python `pickle`**. This means any type that supports `pickle` (has `__reduce__`, `__getstate__`, or is otherwise picklable) can be marked `immutable`.

**Good candidates for `immutable`:**
- Primitive/value types (`int`, `str`, `bytes`, `datetime`, etc.)
- Named tuple / struct result types (`stat_result`, `struct_time`)
- Exception types (subclasses of `BaseException`)
- Enum and constant namespace types
- Data-only types with no I/O methods — even from C extensions, as long as they pickle
- Types that are passed between proxied calls but never called upon to do I/O themselves (e.g., credential objects, event/result containers)

**When NOT to use `immutable`:**
- Types with methods that perform I/O, syscalls, or other non-deterministic operations (use `proxy` instead)
- Types that cannot be pickled (opaque C wrappers with no `__reduce__`, wrapping raw pointers that can't be reconstructed)

```toml
[builtins]
immutable = ["int", "float", "str", "bytes", "bool"]

[_datetime]
immutable = ["datetime", "tzinfo", "timezone"]

# Exception types from C extensions — picklable, no I/O
[_socket]
immutable = ["error", "herror", "gaierror", "timeout"]
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

**When to use:** For enum types, singleton objects, and constant namespaces that should be registered with the proxy system by identity. Use `bind` instead of `immutable` whenever objects are compared by identity (`is`) rather than equality (`==`), or when there should only be one instance of each value.

**Always prefer `bind` over `immutable` for singletons and enums.** With `immutable`, pickle creates a *new* object during replay — equal but not identical (`==` works, `is` fails). With `bind`, the proxy records a reference to the singleton and returns the exact same object during replay (`is` works).

**What it does:** Calls `system.bind(obj)`. For `enum.Enum` subclasses, binds each member individually. For non-enum types, binds the class itself (so its identity is preserved and its class-level constant attributes resolve correctly).

**Good candidates for `bind`:**
- Python `enum.Enum` subclasses
- Cython/C extension enum-like classes with class-level singleton constants (e.g., `StatusCode.ok`, `ConnectivityState.idle`)
- Sentinel objects, flag types, constant namespaces
- Any object that user code might compare with `is`

```toml
[ssl]
bind = ["Options"]

["grpc._cython.cygrpc"]
bind = ["AsyncIOEngine", "StatusCode", "ConnectivityState", "CompletionType", ...]
```

**The decision hierarchy for types:**
1. Has I/O-bearing methods? → `proxy`
2. Is a singleton / enum / constant? → `bind`
3. Is a data carrier / value type? → `immutable`

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
   - Type with I/O-bearing methods → `proxy`
   - Enum / singleton / constant type → `bind` (preserve identity)
   - Type without I/O methods (data carrier, result, event) → `immutable` (prefer over proxy)
   - Exception type → `immutable`
   - Constant → skip (deterministic)
   - Deterministic function → skip
3. **Check for buffer APIs** — methods like `recv_into`, `readinto` that write into caller-provided buffers need `patch_class` with a custom wrapper in `edgecases.py`.
4. **Check for subprocess interaction** — if the module spawns child processes, you may need `wrap` to inject retrace environment variables.
5. **Write the TOML entry** — either add a section to an existing grouped file (e.g., `stdlib.toml`) or create a new single-module file in `modules/`.

### Adding a third-party library

1. **Identify the C extension module** — the `.so`/`.pyd` file that contains the C code. This is what you proxy, not the pure Python wrapper modules.
2. **Enumerate the C extension's exports** using the inspection technique.
3. **Identify the I/O boundary** — which types/functions are the entry points for I/O operations? These are typically:
   - Connection/channel types (e.g., `Channel`, `Connection`)
   - Result/cursor types (e.g., `Cursor`, `Result`)
   - Factory functions (e.g., `connect()`)
4. **Include all types in the call chain** — if a proxied type's method returns another type that also has I/O methods, proxy that type too. Example: `psycopg2._psycopg` proxies both `connection` and `cursor` because `connection.cursor()` returns a `cursor` with its own I/O methods.
5. **Write the TOML entry** — create a new `.toml` file in `modules/` named after the C extension module (e.g., `grpc._cython.cygrpc.toml`). Add `package = "..."` if the library needs version-aware loading.

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

## Buffer Protocol and Memoryview Types

Many C extension methods use Python's buffer protocol — they accept or write into `memoryview`, `bytearray`, or any object that exposes a C-level buffer. These methods create specific challenges for record/replay that require special handling.

### The Problem

The proxy system works by intercepting function calls and recording their **return values**. But buffer-writing methods don't return the data — they mutate a caller-provided buffer in place. During replay, the proxy has no recorded data to write back into the buffer, and the real C function can't run (there's no live connection/file/socket).

Consider `socket.recv_into(buffer)`:
1. The caller allocates a `bytearray` or `memoryview`
2. The C method writes received data directly into that buffer's memory
3. The return value is just the byte count, not the actual data

If we proxy this naively, during recording we capture the byte count but lose the actual data. During replay, we return the byte count but the buffer stays empty.

A secondary problem: methods that accept buffer-like inputs (`memoryview`, `bytearray`) for writing. A `memoryview` is a reference to someone else's memory — it can't be serialized/deserialized by the proxy as a standalone value. The proxy needs the raw bytes, not the memory reference.

### The Solution: Redirect to Data-Returning Variants

The pattern is to replace buffer-writing methods with wrappers that:
1. Call the **data-returning variant** of the same operation (e.g., `recv` instead of `recv_into`)
2. Copy the returned data into the caller's buffer
3. Return the byte count as the original method would

This way the proxy captures the actual data (via the `recv` call), and the buffer gets filled correctly during both recording and replay.

### How to Identify Methods That Need This

Look for these patterns when auditing a module:

| Indicator | Examples |
|---|---|
| Methods with `_into` suffix | `recv_into`, `recvfrom_into`, `recvmsg_into`, `readinto`, `readinto1` |
| Methods with a `buffer` / `buf` parameter | `_ssl._SSLSocket.read(len, buffer)` |
| C signature uses `Py_buffer*` | Check CPython source or docs for "buffer" parameter types |
| Docs say "read into a pre-allocated buffer" | io, socket, ssl, mmap modules |

### Existing Wrappers in `edgecases.py`

#### `recv_into` / `recvfrom_into` — `_socket.socket`

Redirects to `recv`/`recvfrom`, copies data into the buffer:

```python
def recv_into(target):
    @functools.wraps(target)
    def wrapper(self, buffer, nbytes=0, flags=0):
        data = self.recv(len(buffer) if nbytes == 0 else nbytes, flags)
        buffer[0:len(data)] = data
        return len(data)
    return wrapper

def recvfrom_into(target):
    @functools.wraps(target)
    def wrapper(self, buffer, nbytes=0, flags=0):
        data, address = self.recvfrom(len(buffer) if nbytes == 0 else nbytes, flags)
        buffer[0:len(data)] = data
        return len(data), address
    return wrapper
```

Config:

```toml
[_socket.patch_class.socket]
recvfrom_into = "retracesoftware.install.edgecases.recvfrom_into"
recv_into = "retracesoftware.install.edgecases.recv_into"
recvmsg_into = "retracesoftware.install.edgecases.recvmsg_into"
```

#### `readinto` — `io.FileIO`, `io.BufferedReader`, `io.BufferedRandom`

Redirects to `read`, copies data into the buffer. Uses `buffer.nbytes` to determine the read size:

```python
def readinto(target):
    @functools.wraps(target)
    def wrapper(self, buffer):
        bytes = self.read(buffer.nbytes)
        buffer[:len(bytes)] = bytes
        return len(bytes)
    return wrapper
```

#### `read` — `_ssl._SSLSocket`

SSL's `read` has a dual interface: `read(len)` returns bytes, but `read(len, buffer)` writes into a buffer and returns the byte count. The wrapper handles both modes:

```python
def read(target):
    @functools.wraps(target)
    def wrapper(self, *args):
        if len(args) == 0:
            return target(self)
        else:
            buflen = args[0]
            data = target(self, buflen)

            if len(args) == 1:
                return data              # read(len) → return bytes
            else:
                buffer = args[1]
                buffer[0:len(data)] = data
                return len(data)          # read(len, buf) → return count
    return wrapper
```

#### `write` — outbound buffer conversion

For methods that accept `memoryview` or buffer-like objects for sending/writing, the wrapper converts to `bytes` before proxying so the data can be serialized:

```python
def write(target):
    @functools.wraps(target)
    def wrapper(self, byteslike):
        return target(byteslike.tobytes())
    return wrapper
```

### Writing a New Buffer Wrapper

When you encounter a new `*_into` or buffer-writing method, follow this template:

1. **Find the data-returning equivalent.** Most buffer-writing methods have a counterpart:

   | Buffer method | Data-returning equivalent |
   |---|---|
   | `socket.recv_into(buf)` | `socket.recv(bufsize)` |
   | `socket.recvfrom_into(buf)` | `socket.recvfrom(bufsize)` |
   | `io.FileIO.readinto(buf)` | `io.FileIO.read(size)` |
   | `io.BufferedReader.readinto(buf)` | `io.BufferedReader.read(size)` |
   | `io.BufferedReader.readinto1(buf)` | `io.BufferedReader.read1(size)` |
   | `ssl.SSLSocket.read(n, buf)` | `ssl.SSLSocket.read(n)` |
   | `mmap.mmap.readinto(buf)` | `mmap.mmap.read(n)` |

2. **Write the wrapper** in `edgecases.py`:

   ```python
   def my_readinto(target):
       @functools.wraps(target)
       def wrapper(self, buffer):
           data = self.read(len(buffer))  # call the data-returning variant
           buffer[:len(data)] = data      # copy into caller's buffer
           return len(data)               # return byte count
       return wrapper
   ```

3. **Register it** in the `typewrappers` dict in `edgecases.py`:

   ```python
   typewrappers = {
       'my_module': {
           'MyType': {
               'readinto': my_readinto
           }
       },
       ...
   }
   ```

4. **Add the `patch_class` entry** in the appropriate `.toml` file:

   ```toml
   # In a grouped file:
   [my_module.patch_class.MyType]
   readinto = "retracesoftware.install.edgecases.my_readinto"

   # Or in a single-module file (my_module.toml):
   [patch_class.MyType]
   readinto = "retracesoftware.install.edgecases.my_readinto"
   ```

### Common Buffer Methods by Module

A reference of buffer-protocol methods across modules that need or may need wrappers:

| Module | Type | Method | Status |
|---|---|---|---|
| `_socket` | `socket` | `recv_into` | Wrapped |
| `_socket` | `socket` | `recvfrom_into` | Wrapped |
| `_socket` | `socket` | `recvmsg_into` | Wrapped (raises NotImplementedError) |
| `_ssl` | `_SSLSocket` | `read(n, buffer)` | Wrapped |
| `_io` | `FileIO` | `readinto` | Wrapper exists in edgecases.py |
| `_io` | `BufferedReader` | `readinto` | Wrapper exists in edgecases.py |
| `_io` | `BufferedReader` | `readinto1` | **Needs wrapper** |
| `_io` | `BufferedRandom` | `readinto` | Wrapper exists in edgecases.py |
| `_io` | `BufferedRandom` | `readinto1` | **Needs wrapper** |
| `_io` | `BufferedRWPair` | `readinto` | **Needs wrapper** (if `_io` is proxied) |
| `_io` | `BufferedRWPair` | `readinto1` | **Needs wrapper** (if `_io` is proxied) |
| `mmap` | `mmap` | `readinto` | **Needs wrapper** (if `mmap` is proxied) |
| `hashlib` | `hash` | `update(buf)` | Not needed — deterministic, accepts buffer but doesn't write |
| `struct` | — | `pack_into(buf)` | Not needed — deterministic |
| `array` | `array` | `frombytes(buf)` | Not needed — deterministic |

### Why `memoryview` Is Marked Immutable

In `stdlib.toml`, `memoryview` is listed under `[builtins] immutable`. This tells the proxy system to treat `memoryview` instances as serializable values rather than mutable proxy objects. This is necessary because:

- A `memoryview` is a reference to another object's buffer — it can't be independently reconstructed during replay
- The proxy system needs to serialize the underlying bytes when recording a memoryview argument, not the memory reference
- Marking it immutable ensures the proxy copies the bytes out for serialization rather than trying to track it as a live proxy object

### Outbound Buffers (Write Direction)

The problem also applies in reverse: methods that accept buffer-like objects for **sending** data. If user code passes a `memoryview` to `socket.send(mv)` or `ssl.write(mv)`, the proxy needs to serialize the actual bytes, not the memoryview reference.

This is generally handled automatically by the proxy's serialization layer — when it encounters a `memoryview` argument, it should call `.tobytes()` to get a serializable `bytes` object. If a specific method has issues, add a wrapper that converts explicitly:

```python
def write(target):
    @functools.wraps(target)
    def wrapper(self, byteslike):
        return target(byteslike.tobytes())
    return wrapper
```

---

## Edge Cases

### Module names with dots

For modules with dots in their names (like third-party packages), create a **single-module file** named after the module:

```
modules/psycopg2._psycopg.toml
modules/grpc._cython.cygrpc.toml
```

The filename (minus `.toml`) becomes the module name. No quoting needed — the resolver uses the filename stem directly.

Alternatively, in a grouped file, quote the TOML section key:

```toml
["psycopg2._psycopg"]
proxy = ["connect", "cursor", "connection"]
```

### Sub-key sections with dots

For `type_attributes`, `patch_class`, and `wrap` on dotted module names in grouped files, the dot-separated path must be correctly structured:

```toml
["_pydevd_bundle.pydevd_cython".type_attributes]
ThreadTracer.disable = ["__call__"]
```

### Platform-specific modules

Some modules only exist on certain platforms. The patcher only patches names that exist in the module's namespace (`if name in namespace`), so listing a function that doesn't exist on the current platform is safe — it will simply be skipped.

### Optional dependencies

Third-party library entries are safe to include even if the library isn't installed. For single-module files with `package = "..."`, the resolver checks if the package is installed and silently skips the file if not. For grouped files, entries are only applied when the module is actually imported.

### Existing entries that don't match the module

If a TOML section key (or filename) doesn't match any importable module name, the entry will never trigger. The patcher matches by `__name__` in the module's namespace.

---

## Checklist

Before submitting a new module config entry, verify:

- [ ] **Module name is spelled correctly** (filename or TOML section key must match `module.__name__`)
- [ ] **All listed names exist in the module** (run `dir(module)` to confirm)
- [ ] **`immutable` is preferred over `proxy`** — only use `proxy` for types that have methods which actually perform I/O or non-deterministic operations. Data-only types, result containers, events, exceptions, and enums should be `immutable`.
- [ ] **I/O-bearing types are proxied**, not just factory functions
- [ ] **Return types of proxied methods are also covered** — either `proxy` (if they have I/O methods) or `immutable` (if they're data carriers)
- [ ] **Buffer-writing methods** have `patch_class` wrappers if needed
- [ ] **Exception types** are marked `immutable` if they exist in the module
- [ ] **Enum / singleton / constant types** use `bind` (not `immutable`) to preserve identity
- [ ] **Immutable types are picklable** — since the serializer delegates to `pickle` for non-primitive types, verify with `pickle.dumps(instance)` that instances can be serialized
- [ ] **Comments explain the rationale** — why this module needs proxying, what C library it wraps, what I/O it does
