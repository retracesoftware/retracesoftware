# Failing dockertests — snapshot 2026-04-29 12:37 CEST

This folder is a frozen snapshot of the dockertest scenarios that fail under the
current commit on branch `backup/wip-2026-04-04-system-io-save`
(last touching commit: `a34d62a Refine replay boundary handling`, 2026-04-29).

It exists so Nathan's AI can debug them without having to chase the live
`dockertests/tests/` tree (which keeps moving).

The `test.py` (and `requirements.txt`, plus `app.py` for `flask_test`) inside
each subdirectory here are **byte-identical copies** of the live, committed,
pushed source. The authoritative location remains:

```
retracesoftware/dockertests/tests/<name>/test.py
```

All 11 source files are committed and pushed to origin
(`origin/backup/wip-2026-04-04-system-io-save`); this folder is a convenience
mirror, not a fork.

---

## Environment used to reproduce

- Python: 3.12.0 (pyenv)
- venv: `/Users/danielpatrascanu/cookbook/examples/invoice-parser/.venv-retrace-py3120-clean`
- retracesoftware: built from current HEAD (editable install)
- Go replay binary: `retracesoftware/.retrace-replay-bin`
- Run config: `RETRACE_CONFIG=debug`

---

## Bash flow used per test

This is the canonical "manual" run pattern, identical for every test except
`flask_test` (which needs two terminals).

```bash
source /Users/danielpatrascanu/cookbook/examples/invoice-parser/.venv-retrace-py3120-clean/bin/activate
cd /Users/danielpatrascanu/cookbook/examples/invoice-parser/retracesoftware/dockertests/tests/<NAME>
[ -f requirements.txt ] && python -m pip install -q -r requirements.txt
rm -f test.retrace; rm -rf test.d
RETRACE_CONFIG=debug python test.py
./test.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording test.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"
./test.d/${ROOT_PID}.bin
```

Substitute `<NAME>` with the directory name (e.g. `asgiref_test`).

---

## Distinct bugs causing the 11 failures

After grouping by error fingerprint, **3 distinct retrace bugs** + **1 known
pre-existing limitation** account for all 11 failures:

### Bug A — `RuntimeError: bind marker returned when bind was expected`
Source: `src/retracesoftware/proxy/io.py:238`

Triggered during teardown / pooled-lock acquisition flows. Same stack frames in
every case:

```
proxy/io.py:1083 in call           (checkpoint_call)
proxy/io.py:1057 in checkpoint     (expect_message(CheckpointMessage))
proxy/io.py:945  in expect_message (read_message)
proxy/io.py:339  in read           (_message_from_tag)
proxy/io.py:238  in read           (raise RuntimeError("bind marker returned ..."))
```

Affects 6 tests (in this snapshot): `asgiref_test`, `requests_test`,
`threading_stress_test`, `coreapi_test`, `flask_test`, `asynclru_test` (run-to-run
flaky alongside Bug B).

A single fix at the proxy/io.py read site is the highest-leverage thing in this
batch.

### Bug B — `Checkpoint difference: <CheckpointMessage> was expecting 'SYNC'`
Source: same `proxy/io.py` checkpoint stream

Affects 1 test deterministically: `asynclru_test` (run 1). On subsequent runs
the same test fails with Bug A instead — the two error modes are interchangeable
on async-LRU + httpx flow.

### Bug C — replay reads dead PIDs from live OS state
Affects 1 test: `memray_test`. The test calls `psutil.Process()` which records
the live PID at record time, then on replay psutil tries macOS `task_info`
against a PID that no longer exists. Either materialize `psutil.Process` more
aggressively, or treat this as an inherently-non-replayable test.

### Bug D — `Dispatcher: too many threads waiting for item`
Affects 1 test: `pyopenssl_test`. Thread-race in `proxy/io.py` replay
dispatcher when the test's daemon SSL server thread is awaiting work. Sometimes
hangs forever, sometimes raises this exception.

### Pre-existing limitation — multiprocessing/fork blocker
Affects 1 test: `appnope_test`. Records a parent that forks via
`multiprocessing.Process`. On record, the child process is auto-activated by
retrace's `.pth` and gets `python -c` it can't parse → `Error: Not a Python
script: -c`. Already in the user's exclusion list — not a new bug.

### Reproduces inconsistently in this environment
Two tests reported as failing on Daniel's machine but pass 3/3 on the sandbox
re-run today: `opentelemetry_test`, `cachecontrol_test`. Daniel's stack traces
for these are included below (verbatim) so Nathan's AI can attempt
reproduction directly.

---

## Per-test details

Each entry below contains:
- live path (the authoritative location in the repo)
- exact bug bucket
- reproducer command
- verbatim error trace from Daniel's run on 2026-04-29 12:37 CEST

### asgiref_test
- live: `retracesoftware/dockertests/tests/asgiref_test/test.py`
- bug: **A** (`bind marker returned when bind was expected`)
- run: standard bash flow, no env overrides
- error (verbatim, Daniel):
  ```
  File "test.py", line 21, in <module>
    asyncio.run(test_sync_to_async())
  ...
  File "/.../asyncio/selector_events.py", line 111, in _close_self_pipe
    self._remove_reader(self._ssock.fileno())
  File "/.../proxy/io.py", line 1083, in call
    return checkpoint_call(*args, **kwargs)
  File "/.../proxy/io.py", line 1057, in checkpoint
    message = expect_message(CheckpointMessage)
  File "/.../proxy/io.py", line 945, in expect_message
    message = read_message()
  File "/.../proxy/io.py", line 339, in read
    return _message_from_tag(self._read(), self._read, self._thread_id)
  File "/.../proxy/io.py", line 238, in read
    raise RuntimeError("bind marker returned when bind was expected")
  RuntimeError: bind marker returned when bind was expected
  Task was destroyed but it is pending!
  ...
  ValueError: Invalid file descriptor: -1
  replay: replay exited: exit status 1
  ```

### appnope_test
- live: `retracesoftware/dockertests/tests/appnope_test/test.py`
- bug: **pre-existing limitation** (multiprocessing/fork blocker, child Python
  subprocess being auto-activated by retrace `.pth`)
- run: standard bash flow
- error (verbatim, Daniel — record stage):
  ```
  Error: Not a Python script: -c
  (works completely fine without retrace)
  ```
- note: parent's recording is produced, but the forked child's recording is
  what fails. Sandbox re-run sees the same root cause surface as a replay-time
  `posix.close` vs `posix._exit` divergence followed by
  `Could not read 1 bytes from tracefile`.

### opentelemetry_test
- live: `retracesoftware/dockertests/tests/opentelemetry_test/test.py`
- bug: thread-race in `proxy/io.py` reader during exporter shutdown — likely a
  variant of Bug A but in the dispatcher's read path rather than checkpoint
- run: standard bash flow
- error (verbatim, Daniel):
  ```
  Created first span
  Generated span 0
  Generated span 1
  Generated span 2
  Test complete. All spans processed through fake exporter.
  Exception in thread OtelBatchSpanRecordProcessor:
  Traceback (most recent call last):
    File "/.../threading.py", line 1052, in _bootstrap_inner
      self.run()
    File "/.../opentelemetry/sdk/_shared_internal/__init__.py", line 168, in worker
      sleep_interrupted = self._worker_awaken.wait(self._schedule_delay)
    File "/.../threading.py", line 631, in wait
      with self._cond:
    File "/.../threading.py", line 282, in __exit__
      return self._lock.__exit__(*args)
    File "/.../proxy/system.py", line 619, in int_proxytype
      self.checkpoint(f'creating internal proxytype for {cls}')
    File "/.../proxy/io.py", line 1057, in checkpoint
      message = expect_message(CheckpointMessage)
    ...
    File "/.../proxy/io.py", line 234, in read
      self.consume_pending_closes()
    File "/.../proxy/io.py", line 217, in consume_pending_closes
      value = peek(resolve=False)
    File "/.../proxy/io.py", line 188, in _peek_item
      buffer.append(self._read())
    File "/.../proxy/io.py", line 304, in read
      return self._next_item()
    File "/.../proxy/io.py", line 298, in _next_item
      _, item = self._dispatcher.next(...)
  RuntimeError: Could not read: 1 bytes from tracefile with timeout: 1000 milliseconds
  [EXPORT] Shutdown exporter
  ```
- reproduction note: this fails on Daniel's machine but passed 3/3 on a clean
  sandbox re-run today. Probably a thread-scheduling race tied to the worker
  daemon thread's `wait()` racing with the main thread's exit.

### requests_test
- live: `retracesoftware/dockertests/tests/requests_test/test.py`
- bug: **A** (`bind marker returned when bind was expected`)
- run: standard bash flow (network: httpbin.org)
- error (verbatim, Daniel):
  ```
  File "test.py", line 24, in <module>
    test_requests_with_io()
  File "test.py", line 19, in test_requests_with_io
    fetch_patient_data()
  File "test.py", line 8, in fetch_patient_data
    response = requests.get(URL, timeout=10)
  File "/.../requests/api.py", line 73, in get
    return request("get", url, params=params, **kwargs)
  File "/.../requests/api.py", line 58, in request
    with sessions.Session() as session:
  File "/.../requests/sessions.py", line 458, in __exit__
    self.close()
  File "/.../requests/sessions.py", line 800, in close
    v.close()
  File "/.../requests/adapters.py", line 520, in close
    self.poolmanager.clear()
  File "/.../urllib3/poolmanager.py", line 288, in clear
    self.pools.clear()
  File "/.../urllib3/_collections.py", line 142, in clear
    with self.lock:
  File "/.../proxy/io.py", line 1083, in call
    return checkpoint_call(*args, **kwargs)
  ... [Bug A stack] ...
  RuntimeError: bind marker returned when bind was expected
  replay: replay exited: exit status 1
  ```

### threading_stress_test
- live: `retracesoftware/dockertests/tests/threading_stress_test/test.py`
- bug: **A** (`bind marker returned when bind was expected`), with cascading
  desync handler chain
- run: standard bash flow
- error (verbatim, Daniel):
  ```
  File "/.../proxy/io.py", line 1057, in checkpoint
    message = expect_message(CheckpointMessage)
  File "/.../proxy/io.py", line 973, in expect_message
    on_desync(message, expected_type)
  File "/.../proxy/io.py", line 874, in default_desync_handler
    os._exit(1)
  ... [chains through the desync handler several times] ...
  File "/.../proxy/io.py", line 238, in read
    raise RuntimeError("bind marker returned when bind was expected")
  RuntimeError: bind marker returned when bind was expected
  replay: replay exited: exit status 1
  ```

### coreapi_test
- live: `retracesoftware/dockertests/tests/coreapi_test/test.py`
- bug: **A** (`bind marker returned when bind was expected`)
- run: standard bash flow (network: api.github.com; `setuptools<80` required
  in the venv for `pkg_resources`)
- error (verbatim, Daniel):
  ```
  File "/.../coreapi/transports/http.py", line 379, in transition
    response = session.send(request)
  File "/.../requests/sessions.py", line 706, in send
    r = adapter.send(request, **kwargs)
  File "/.../requests/adapters.py", line 611, in send
    conn = self.get_connection_with_tls_context(...)
  File "/.../requests/adapters.py", line 467, in get_connection_with_tls_context
    conn = self.poolmanager.connection_from_host(...)
  File "/.../urllib3/poolmanager.py", line 317, in connection_from_host
    return self.connection_from_context(request_context)
  File "/.../urllib3/poolmanager.py", line 342, in connection_from_context
    return self.connection_from_pool_key(pool_key, request_context=request_context)
  File "/.../urllib3/poolmanager.py", line 354, in connection_from_pool_key
    with self.pools.lock:
  File "/.../proxy/io.py", line 1083, in call
    return checkpoint_call(*args, **kwargs)
  ... [Bug A stack] ...
  RuntimeError: bind marker returned when bind was expected
  replay: replay exited: exit status 1
  ```

### flask_test
- live: `retracesoftware/dockertests/tests/flask_test/test.py` (client) +
  `retracesoftware/dockertests/tests/flask_test/app.py` (server, post-`a34d62a`)
- bug: **A** (`bind marker returned when bind was expected`)
- run: requires two terminals.
  - Terminal 1: `python app.py` (unretraced server)
  - Terminal 2:
    ```bash
    cd retracesoftware/dockertests/tests/flask_test
    rm -f test.retrace; rm -rf test.d
    RETRACE_CONFIG=debug python test.py
    ./test.retrace --extract
    ROOT_PID=$(python -m retracesoftware --recording test.retrace --list_pids | head -1)
    ./test.d/${ROOT_PID}.bin
    ```
  - If port 5000 is busy on macOS (AirPlay): `PORT=5050 python app.py` and
    `SERVER_URL=http://localhost:5050 RETRACE_CONFIG=debug python test.py`
- error (verbatim, Daniel):
  ```
  File "/.../proxy/io.py", line 1083, in call
    return checkpoint_call(*args, **kwargs)
  File "/.../proxy/io.py", line 1057, in checkpoint
    message = expect_message(CheckpointMessage)
  ... [Bug A stack] ...
  RuntimeError: bind marker returned when bind was expected
  replay: replay exited: exit status 1
  ```

### asynclru_test
- live: `retracesoftware/dockertests/tests/asynclru_test/test.py`
- bug: **B** on first run (`expecting 'SYNC'`), flips to **A** on subsequent
  runs of the same test — both modes valid, both deterministic per-process
  but the choice is non-deterministic across processes.
- run: standard bash flow (network: jsonplaceholder.typicode.com)
- error (verbatim, Daniel):
  ```
  Cache hit for clinician 3
  Fetching availability for clinician 4...
  First fetch result: {'clinician_id': '4', 'available': True, ...}
  Second fetch result (should be cached): {'clinician_id': '4', 'available': True, ...}
  Cache hit for clinician 4
  Fetching availability for clinician 5 to test eviction...
  Third fetch result (cache may be evicted for '1'): {'clinician_id': '1', 'available': False, ...}
  Checkpoint difference: <retracesoftware.protocol.messages.CheckpointMessage object at 0x...> was expecting 'SYNC'
  replay: replay exited: exit status 1
  ```

### cachecontrol_test
- live: `retracesoftware/dockertests/tests/cachecontrol_test/test.py`
- bug: reported as `replay error` by Daniel; passes 3/3 in clean sandbox re-run
  today. May be environment-specific (cachecontrol/requests version skew?).
- run: standard bash flow
- Daniel's log: only "replay error" (no traceback captured). Nathan's AI
  should re-run on a clean venv to confirm or not.

### memray_test
- live: `retracesoftware/dockertests/tests/memray_test/test.py`
- bug: **C** (`psutil.NoSuchProcess: process PID not found`)
- run: standard bash flow
- error (verbatim, Daniel):
  ```
  5. Testing memory usage reporting...
  Traceback (most recent call last):
    File "<frozen runpy>", line 198, in _run_module_as_main
    File "<frozen runpy>", line 88, in _run_code
    File "/.../retracesoftware/__main__.py", line 519, in <module>
      main()
    File "/.../retracesoftware/__main__.py", line 516, in main
      replay(args)
    File "/.../retracesoftware/__main__.py", line 333, in replay
      system.run(run_python_command, header["argv"])
    File "/.../proxy/system.py", line 533, in run
      return run_internal(*args, **kwargs)
    File "/.../retracesoftware/run.py", line 88, in run_python_command
      runpy.run_path(script_path, run_name="__main__")
    File "/.../install/importhook.py", line 88, in _exec_and_patch_entry
      _orig_exec(source, globals, locals)
    File "test.py", line 72, in <module>
      test_memray_profiling()
    File "test.py", line 61, in test_memray_profiling
      process = psutil.Process()
    File "/.../psutil/__init__.py", line 314, in __init__
      self._init(pid)
    File "/.../psutil/__init__.py", line 360, in _init
      raise NoSuchProcess(pid, msg=msg) from None
  psutil.NoSuchProcess: process PID not found (pid=17956)
  replay: replay exited: exit status 1
  ```

### pyopenssl_test
- live: `retracesoftware/dockertests/tests/pyopenssl_test/test.py`
- bug: **D** (`Dispatcher: too many threads waiting for item`) — flaky thread
  race in proxy/io.py replay dispatcher.
- run: standard bash flow
- error (verbatim, Daniel):
  ```
  File "/.../proxy/io.py", line 234, in read
    return ResultMessage(reader(), thread_id=current_thread_id)
    ...
  File "/.../proxy/io.py", line 217, in consume_pending_closes
    self.consume_pending_closes()
    value = peek(resolve=False)
  File "/.../proxy/io.py", line 188, in _peek_item
    buffer.append(self._read())
  File "/.../proxy/io.py", line 304, in read
    return self._next_item()
  File "/.../proxy/io.py", line 298, in _next_item
    _, item = self._dispatcher.next(...)
  RuntimeError: Dispatcher: too many threads waiting for item
  replay: replay exited: exit status 1
  ```

---

## Suggested debugging order for Nathan's AI

1. **Bug A first** — single fingerprint, single read site (`proxy/io.py:238`),
   biggest blast radius (6 tests). Almost all instances trigger inside a
   `with <pool>.lock:` or asyncio shutdown path. Likely root cause: replay
   reading the next tape item but seeing a `_BindOpenEvent` where a checkpoint
   was expected, or vice-versa, when a recording-time lock acquisition was
   actually a re-entry the replayer didn't model.
2. **Bug D** (`Dispatcher: too many threads waiting for item`) — thread race
   in the dispatcher's wait queue. May share a root cause with Bug A. Hangs +
   spurious raises both seen.
3. **Bug B** (`expecting 'SYNC'`) — same proxy/io.py file, narrower trigger.
   Likely a sibling of A, surfacing on the async-LRU + httpx code path.
4. **Bug C** (psutil dead PID) — separate concern. Either materialize
   `psutil.Process` so it doesn't hit live syscalls on replay, or document
   memray_test as inherently non-replayable.
5. **appnope** — already-known multiprocessing/fork limitation; out of scope
   for this batch.

---

## Provenance check for Nathan

Snapshot taken from clean working tree on
`backup/wip-2026-04-04-system-io-save` at:

- HEAD: `a34d62a Refine replay boundary handling` (2026-04-29)
- in sync with `origin/backup/wip-2026-04-04-system-io-save`
- all 11 `test.py` files committed and pushed (no local modifications)

If anything in this folder differs from the live `dockertests/tests/<name>/`,
the live tree is the source of truth.
