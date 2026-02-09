# modules.toml Audit

Systematic audit of Python's standard library and C extension modules for non-deterministic behavior, compared against the current `modules.toml` configuration.

---

## Bug in Existing Config

### `[fnctl]` → `[fcntl]` (typo)

Line 260 has the module name transposed: `fnctl` instead of `fcntl`. The functions *inside* are spelled correctly (`fcntl`, `flock`, `ioctl`, `lockf`), but the section header is wrong. If the patcher resolves this via `importlib.import_module("fnctl")`, it will fail — meaning fcntl has likely never been proxied.

---

## Missing Modules

### 1. `_io` / `io` — CRITICAL

`_io` types (`FileIO`, `BufferedReader`, `BufferedWriter`, `BufferedRandom`, `TextIOWrapper`) perform syscalls (`open`, `read`, `write`, `close`, `lseek`, `fstat`) **directly in C**, completely bypassing proxied `posix.*` functions. Any program that does `open("foo.txt").read()` has unrecorded I/O at the C level.

The commented-out config and existing `edgecases.readinto` wrappers show this was already identified but hasn't been enabled.

```toml
[_io]
proxy = ["open", "open_code"]
# Needs patch_type on: FileIO, BufferedReader, BufferedWriter, BufferedRandom, TextIOWrapper
```

### 2. `_uuid` — MEDIUM

`uuid.uuid1()` calls `_uuid.generate_time_safe()` directly in C (uses MAC address + timestamp). Does NOT flow through proxied `time` or `posix`. Note: `uuid.uuid4()` is fine — it uses `os.urandom`, which is proxied via `posix`.

```toml
[_uuid]
proxy = ["generate_time_safe"]
```

### 3. `pwd` — MEDIUM

`getpwnam`, `getpwuid`, `getpwall` make direct C calls to the user database (`getpwnam_r`, `getpwuid_r`, `getpwent`). Does NOT go through `posix`. Used by `os.path.expanduser()`, `getpass.getuser()`, and any code that looks up user info.

```toml
[pwd]
proxy = ["getpwnam", "getpwuid", "getpwall"]
```

### 4. `grp` — MEDIUM

Same pattern as `pwd` — direct C calls to the group database.

```toml
[grp]
proxy = ["getgrnam", "getgrgid", "getgrall"]
```

### 5. `_locale` — MEDIUM

`setlocale`, `localeconv`, `nl_langinfo` make direct C calls. Locale can affect `str.lower()`, `str.upper()`, collation, number formatting, etc.

```toml
[_locale]
proxy = ["setlocale", "localeconv", "nl_langinfo"]
```

### 6. `mmap` — MEDIUM

`mmap()` creates memory-mapped file regions via C-level `mmap()` syscall. All I/O on the mmap object (`read`, `write`, `__getitem__`, `flush`) bypasses `posix`.

```toml
[mmap]
proxy = ["mmap"]
```

### 7. `termios` — MEDIUM

All functions are direct C-level `ioctl`/`tcsetattr`/`tcdrain` syscalls, not going through `posix`.

```toml
[termios]
proxy = ["tcgetattr", "tcsetattr", "tcsendbreak", "tcdrain", "tcflush", "tcflow"]
```

---

## Missing Functions in Existing Modules

### `[_thread]` — missing 4 functions

Currently proxies: `allocate`, `allocate_lock`, `RLock`. (`start_new_thread` / `start_new` are already handled separately in `startthread.py`.)

| Function | Why it needs proxying |
|---|---|
| `get_ident` | Returns OS-assigned thread ID — non-deterministic across runs |
| `get_native_id` | Returns kernel thread ID — non-deterministic across runs |
| `_count` | Returns number of alive threads — depends on thread scheduling |
| `_set_sentinel` | Creates a lock released on thread exit — tied to thread lifecycle |

```toml
[_thread]
proxy = ["allocate", "allocate_lock", "RLock", "get_ident", "get_native_id", "_count", "_set_sentinel"]
```

---

## Covered Transitively (no action needed)

These modules are pure Python; their non-determinism flows through already-proxied C modules.

| Module | Covered by |
|---|---|
| `platform` | `posix.uname`, `_socket.gethostname`, `_posixsubprocess.fork_exec` |
| `secrets` | `posix.urandom` |
| `tempfile` | `posix.*` + `_random.Random` (assuming `_io` gets proxied) |
| `shutil` | `posix.*` (assuming `_io` gets proxied) |
| `subprocess` | `_posixsubprocess.fork_exec` |
| `pty` | `posix.openpty`, `posix.fork` |
| `getpass` | Covered once `_io` + `pwd` are proxied |
| `gc` | Already handled via `GCHook` + `gc.callbacks` in proxy system |
| `atexit` | Already handled in `run.py` |
| `_weakref` | Already patched via `patch_weakref()` in proxy system |

---

## Low Priority

Non-deterministic, but rarely impacts program logic or requires case-by-case handling.

| Module | Notes |
|---|---|
| `resource` | `getrusage`/`getrlimit` — monitoring only, rarely used for branching |
| `_asyncio` | Likely covered transitively if `select`/`_socket`/`time` are proxied, but complex to verify |
| `_ctypes` | Cannot be generically proxied — each ctypes-based library needs its own strategy |
| `_curses` | Only relevant for TUI apps, hundreds of functions |
| `syslog` | Side-effect only (writes to log), no return-value non-determinism |
| `readline` | Interactive terminal input only |
| `_tracemalloc` | Debugging/profiling only |
| `_lsprof` | Profiling timing data only |
| `_warnings` | Side-effect only for most programs |

---

## Confirmed Deterministic (no action needed)

All functions in these modules are deterministic given the same inputs. No system calls, no I/O, no randomness.

`_heapq`, `_struct`, `_pickle`, `_hashlib`, `_blake2`, `_sha256`, `_sha512`, `_md5`, `_zlib`, `_bz2`, `_lzma`, `_csv`, `_json`, `_decimal`, `_contextvars`, `_abc`, `_functools`, `_operator`, `_statistics`, `_bisect`, `marshal`, `_collections`, `_queue`
