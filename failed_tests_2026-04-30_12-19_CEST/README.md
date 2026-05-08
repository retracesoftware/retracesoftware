# Failing dockertests — snapshot 2026-04-30 12:19 CEST

Repo HEAD when these were captured: **`d8716bd Add psutil replay config coverage`**
Branch: `backup/wip-2026-04-04-system-io-save`

This folder is a self-contained drop for whoever is debugging Retrace next.
Each subdirectory is a verbatim copy of the matching `dockertests/tests/<name>/`
test files (`test.py`, and `requirements.txt` when one exists).

This snapshot **replaces** the earlier `failed_tests_2026-04-30_10-20_CEST/` folder.

---

## What changed since the 10:20 snapshot (commit `7e01876` → `d8716bd`)

### Fixed

| Test | Was failing with | Likely fix commit |
|------|------------------|-------------------|
| `appnope_test` | replay aborts: `Checkpoint difference: <ResultMessage> was expecting type:CheckpointMessage` | unclear — gone at `d8716bd` |
| `threading_stress_test` | same `<ResultMessage> was expecting type:CheckpointMessage` regression | likely the same fix as `appnope_test` |
| `memray_test` | replay of last subprocess: `psutil.NoSuchProcess: process PID not found (pid=…)` | **`d8716bd Add psutil replay config coverage`** — exact match |

### Still broken (same fingerprint)

- `opentelemetry_test`
- `asynclruio_test`
- `billiard_test`

### Still broken but the terminal error has shifted

- `flask_basic_test` — same root symptom (replay actually serves live HTTP) but now ends with `RuntimeError: bind marker returned when bind was expected` instead of yesterday's `socket._accept vs lambda` checkpoint diff. Could be the same bug, just hitting a different assertion downstream.

### New (regression or newly-discovered)

- `fsspec_test` — passing in earlier batches, now fails on replay with the same tracefile read-timeout shape as `opentelemetry_test`.
- `asynclru_test` — fails with the same dispatcher / asyncio-fd-=-1 shape as `asynclruio_test`.

---

## Environment used to reproduce

* macOS arm64, Python **3.12.0** (pyenv).
* Fresh venv `.venv-retrace-py3120-2026-04-30-1147` built from source at HEAD `d8716bd` (`pip install -e ./retracesoftware --no-build-isolation` in a clean 3.12.0 venv with `meson`, `ninja`, `meson-python`, `setuptools_scm`, `pybind11` installed first).
* Each test was run from inside its own `dockertests/tests/<name>/` after `pip install -r requirements.txt`.
* Replay still requires `RETRACE_SKIP_CHECKSUMS=1` because the wheel ships `AGENTS.md` / `DESIGN.md` files inside `retracesoftware/proxy/`, `retracesoftware/install/`, etc., which trip the file-set checksum comparison.

## Run pattern (CLI changed at `d8716bd`)

```bash
# 1. record  (--recording is now a *flag*; do NOT prefix the script with `python`)
RETRACE_CONFIG=debug python -m retracesoftware --recording test.retrace -- test.py

# 2. extract (no longer in the Python wrapper — invoke the recording itself, which is shebang'd)
./test.retrace --extract

# 3. replay
ROOT_PID=$(python -m retracesoftware --recording test.retrace --list_pids | head -1)
RETRACE_SKIP_CHECKSUMS=1 ./test.d/${ROOT_PID}.bin
```

`flask_basic_test` is interactive — see its section.

---

## Failing tests in this snapshot (6)

| # | Test | Symptom (one line) | Status |
|---|------|--------------------|--------|
| 1 | `opentelemetry_test` | replay aborts after the test logically completes: `RuntimeError: Could not read: 1 bytes from tracefile with timeout: 1000 milliseconds` (now visible mid-stack inside `proxy/io.py:expect_message → consume_pending_closes → peek → next`) | persisting from yesterday |
| 2 | `flask_basic_test` | replay actually serves live HTTP (welcome banner + access-log line); on Ctrl-C terminates with `RuntimeError: bind marker returned when bind was expected` | persisting; **terminal error different** from yesterday |
| 3 | `asynclruio_test` | `RuntimeError: Dispatcher: too many threads waiting for item` followed by asyncio teardown `ValueError: Invalid file descriptor: -1` | persisting from yesterday |
| 4 | `billiard_test` | record OK, extract writes children but **never** the root PID (`zsh: no such file or directory: ./test.d/15596.bin`) | persisting from yesterday — multiprocessing/fork bucket |
| 5 | `fsspec_test` | replay aborts: `RuntimeError: Could not read: 1 bytes from tracefile with timeout: 1000 milliseconds` (caught at `proxy/io.py:peek_buffered` during root replay) | **NEW regression** — same fingerprint as #1 |
| 6 | `asynclru_test` | identical to #3: `Dispatcher: too many threads waiting for item` + asyncio fd=-1 | **NEW regression** — same fingerprint as #3 |

After grouping by fingerprint there are essentially **4 distinct bugs** (probably 3 if the dispatcher-timeout in `opentelemetry`/`fsspec` is a special case of the dispatcher-waiter bug in `asynclru`/`asynclruio`):

- **A.** Tracefile read times out on final-flush / shutdown — `opentelemetry_test`, `fsspec_test`
- **B.** Dispatcher: too many threads waiting + asyncio teardown fd=-1 — `asynclruio_test`, `asynclru_test`
- **C.** Replay falls through to live `socket.accept` for Flask's WSGI loop — `flask_basic_test`
- **D.** Multiprocessing/fork — root PidFile not extracted — `billiard_test`

---

## Verbatim error excerpts (this morning's run, HEAD `d8716bd`)

### 1. `opentelemetry_test`

```
replay run: /path/to/.venv-retrace-py3120/bin/python
            [-m retracesoftware --recording .../opentelemetry_test/test.d/13520.bin]
            (cwd=.../opentelemetry_test)
=== opentelemetry_test ===
Created first span
Generated span 0
Generated span 1
Generated span 2
Exception in thread OtelBatchSpanRecordProcessor:
Traceback (most recent call last):
  File ".../python3.12/threading.py", line 1052, in _bootstrap_inner
    self.run()
  File ".../retracesoftware/src/retracesoftware/proxy/system.py", line 288, in retraced_run
    return self.gate.apply_with('internal', wrapped_run)(*run_args, **run_kwargs)
  File ".../python3.12/threading.py", line 989, in run
    self._target(*self._args, **self._kwargs)
  File ".../site-packages/opentelemetry/sdk/_shared_internal/__init__.py", line 168, in worker
    sleep_interrupted = self._worker_awaken.wait(self._schedule_delay)
  File ".../python3.12/threading.py", line 631, in wait
    with self._cond:
  File ".../python3.12/threading.py", line 282, in __exit__
    return self._lock.__exit__(*args)
  File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 1115, in expect_message
    message = read_message()
  File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 340, in read
    return _message_from_tag(self._read(), self._read, self._thread_id)
  File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 235, in read
    self.consume_pending_closes()
  File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 218, in consume_pending_closes
    value = peek(resolve=False)
  File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 189, in _peek_item
    buffer.append(self._read())
  File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 305, in read
    return self._next_item()
  File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 299, in _next_item
    _, item = self._dispatcher.next(
RuntimeError: Could not read: 1 bytes from tracefile with timeout: 1000 milliseconds
Test complete. All spans processed through fake exporter.
[EXPORT] Shutdown exporter
```

Note again: the test's own `Test complete.` and `[EXPORT] Shutdown exporter` lines print **after** the RuntimeError. That's a final-flush / shutdown ordering problem inside the OpenTelemetry batch-span worker thread on replay — the worker thread is still inside its lock-release path when the dispatcher reports EOF.

The mid-stack frames now go through `proxy/io.py:expect_message → read → consume_pending_closes → _peek_item → _next_item → dispatcher.next` — useful for whoever debugs this; it shows the dispatcher running under a `_thread.Cond.__exit__` on the worker.

### 2. `flask_basic_test`

This is the simple deterministic Flask app under `dockertests/tests/flask_basic_test/`.
Reproduction is **manual** (Ctrl-C at the end of each step):

```bash
# record (open http://127.0.0.1:5000/ in browser, refresh a few times, Ctrl-C)
RETRACE_CONFIG=debug python -m retracesoftware --recording test.retrace -- test.py
./test.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording test.retrace --list_pids | head -1)

# replay (should NOT serve live HTTP, should replay the recorded request loop)
RETRACE_SKIP_CHECKSUMS=1 ./test.d/${ROOT_PID}.bin
```

What we see on **replay** at `d8716bd`:

```
replay run: .../.venv-retrace-py3120-2026-04-30-1147/bin/python
            [-m retracesoftware --recording .../flask_basic_test/test.d/14560.bin]
            (cwd=.../flask_basic_test)
============================================================
Flask basic test running.
Open: http://127.0.0.1:5000/
Refresh the page a few times, then press Ctrl-C here.
============================================================
 * Serving Flask app 'test'
 * Debug mode: off
WARNING: This is a development server. Do not use it in a production deployment...
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit
127.0.0.1 - - [30/Apr/2026 12:01:52] "GET / HTTP/1.1" 200 -

# (after Ctrl-C)
File ".../python3.12/socket.py", line 504, in close
    self._real_close()
File ".../python3.12/socket.py", line 498, in _real_close
    _ss.close(self)
File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 1115, in expect_message
    message = read_message()
File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 340, in read
    return _message_from_tag(self._read(), self._read, self._thread_id)
File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 239, in read
    raise RuntimeError("bind marker returned when bind was expected")
RuntimeError: bind marker returned when bind was expected
```

Two distinct things wrong, same as yesterday:

1. The replayer is **actually serving live HTTP** (welcome banner, real `127.0.0.1 - - "GET /"` access-log line, an interactive port). On replay these should not happen — the proxy layer should be intercepting `socket.accept` / the WSGI loop and feeding the recorded request back deterministically.
2. Once we Ctrl-C, the terminal error has shifted from yesterday's `Checkpoint difference: socket._accept vs _ext_proxytype_from_spec lambda` to `RuntimeError: bind marker returned when bind was expected` deep inside `socket._real_close → expect_message → read`. Looks like the same root cause (missing socket.accept proxy on replay) but is now tripping the bind-marker assertion at teardown instead of at the live `accept()` call.

Strongly suggests `socket.accept` (or the `socket.socket` object that `flask`/`werkzeug` builds during `app.run`) is still not being proxied on replay.

### 3. `asynclruio_test`

```
File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 235, in read
    self.consume_pending_closes()
File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 218, in consume_pending_closes
    value = peek(resolve=False)
File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 189, in _peek_item
    buffer.append(self._read())
File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 305, in read
    return self._next_item()
File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 299, in _next_item
    _, item = self._dispatcher.next(
RuntimeError: Dispatcher: too many threads waiting for item
Exception ignored in: <function BaseEventLoop.__del__ at 0x10467e200>
Traceback (most recent call last):
 File ".../python3.12/asyncio/base_events.py", line 705, in __del__
 File ".../python3.12/asyncio/unix_events.py", line 68, in close
 File ".../python3.12/asyncio/selector_events.py", line 104, in close
 File ".../python3.12/asyncio/selector_events.py", line 111, in _close_self_pipe
 File ".../python3.12/asyncio/selector_events.py", line 294, in _remove_reader
 File ".../python3.12/selectors.py", line 190, in get_key
 File ".../python3.12/selectors.py", line 71, in __getitem__
 File ".../python3.12/selectors.py", line 225, in _fileobj_lookup
 File ".../python3.12/selectors.py", line 42, in _fileobj_to_fd
ValueError: Invalid file descriptor: -1
replay: replay exited: exit status 1
```

Two-stage failure: the dispatcher complains about more replay-side waiters than the recording has, then a follow-up asyncio teardown crash on a closed fd. The second trace is almost certainly a side-effect of the first.

### 4. `billiard_test`

```
=== billiard_test ===
Worker-1 started.
Worker-2 started.
Process Worker-1 starting.
Process Worker-2 starting.
Process Worker-1 finished.
Process Worker-2 finished.
Process-1 has completed with exitcode 0.
Process-2 has completed with exitcode 0.
wrote test.d/index.json
wrote test.d/15598.bin
wrote test.d/15599.bin
extracted 2 PidFile(s) to ./test.d
15598.bin    15599.bin    index.json
ROOT_PID=15596
zsh: no such file or directory: ./test.d/15596.bin
```

`extract` only emits the **child** PIDs (`15598`, `15599`); the **root** (`15596`) is missing. Same multiprocessing/fork bucket as the long-standing blocker.

### 5. `fsspec_test` — **NEW regression**

```
6. Testing file moving...
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File ".../retracesoftware/src/retracesoftware/__main__.py", line 525, in <module>
    main()
  File ".../retracesoftware/src/retracesoftware/__main__.py", line 522, in main
    replay(args)
  File ".../retracesoftware/src/retracesoftware/__main__.py", line 331, in replay
    system.run(run_python_command, header["argv"])
  File ".../retracesoftware/src/retracesoftware/proxy/io.py", line 311, in peek_buffered
    buffered = self._dispatcher.buffered
RuntimeError: Could not read: 1 bytes from tracefile with timeout: 1000 milliseconds
replay: replay exited: exit status 1
```

Same `Could not read: 1 bytes from tracefile with timeout` fingerprint as `opentelemetry_test`, but reached from a different code path (`peek_buffered` during root replay rather than from a worker thread inside an OTel batch processor). Step 6 (`Testing file moving…`) is the operation in `test.py` that finally triggers it. Suggests the same dispatcher/tracefile final-flush bug is reachable from a single-threaded path too.

### 6. `asynclru_test` — **NEW regression**

```
RuntimeError: Dispatcher: too many threads waiting for item
Exception ignored in: <function BaseEventLoop.__del__ at 0x1028ae160>
Traceback (most recent call last):
 File ".../python3.12/asyncio/base_events.py", line 705, in __del__
 File ".../python3.12/asyncio/unix_events.py", line 68, in close
 File ".../python3.12/asyncio/selector_events.py", line 104, in close
 File ".../python3.12/asyncio/selector_events.py", line 111, in _close_self_pipe
 File ".../python3.12/asyncio/selector_events.py", line 294, in _remove_reader
 File ".../python3.12/selectors.py", line 190, in get_key
 File ".../python3.12/selectors.py", line 71, in __getitem__
 File ".../python3.12/selectors.py", line 225, in _fileobj_lookup
 File ".../python3.12/selectors.py", line 42, in _fileobj_to_fd
ValueError: Invalid file descriptor: -1
replay: replay exited: exit status 1
```

Identical to `asynclruio_test`. Same root bug, second trigger.

---

## Suggested triage order for Nathan / Nathan's AI

1. **`flask_basic_test`** (bug C) — most diagnostic. Replay is provably running real `socket.accept` and rebuilding the WSGI listen loop. Whatever proxy registration changed for `socket.accept` (or the post-fork `_thread.lock` re-bind path that the `_ext_proxytype_from_spec` lambda was supposed to substitute) is the smoking gun.
2. **Bug B (`asynclru_test` + `asynclruio_test`)** — `Dispatcher: too many threads waiting for item`. Same fingerprint, two reproducers, both involve asyncio teardown. The asyncio fd=-1 is downstream noise.
3. **Bug A (`opentelemetry_test` + `fsspec_test`)** — final-flush/shutdown read timeout. The fact that `fsspec_test` reaches it from a *single-threaded* root path (no worker thread, no condition-variable interplay) makes it easier to reduce than `opentelemetry_test`. Triage that one first inside this bug.
4. **Bug D (`billiard_test`)** — multiprocessing/fork. Defer unless someone is actively on the multiprocessing work; same long-standing bucket.

## Cross-cutting issue (still present)

Every replay above needs `RETRACE_SKIP_CHECKSUMS=1` because the built wheel still includes
`AGENTS.md` / `DESIGN.md` files under `retracesoftware/proxy/`, `retracesoftware/install/`, etc., which makes the recorded module-file checksum disagree with the replay environment. Either exclude those files from the wheel or relax the checksum comparison for non-`.py`/`.so` files.
