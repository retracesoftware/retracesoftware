# Failing dockertests — snapshot 2026-04-30 10:20 CEST

Repo HEAD when these were captured: **`7e01876 Trace Thread.start at child run boundary`**
Branch: `backup/wip-2026-04-04-system-io-save`

This folder is a self-contained drop for whoever is debugging Retrace next.
Each subdirectory is a verbatim copy of the matching `dockertests/tests/<name>/`
test files (`test.py`, and `requirements.txt` when one exists).

---

## Environment used to reproduce

* macOS (Apple Clang), Python **3.12.0** (pyenv).
* Fresh venv at `.venv-retrace-py3120-2026-04-30-1000` built from the source tree at HEAD `7e01876` (`pip install -e ./retracesoftware` after a full purge of every prior wheel/build/venv).
* Each test was run from inside its own `dockertests/tests/<name>/` directory after `pip install -r requirements.txt`.
* All tests below need **`RETRACE_SKIP_CHECKSUMS=1`** for the replay step because the wheel still bundles `AGENTS.md` / `DESIGN.md` files inside `retracesoftware/proxy/`, `retracesoftware/install/`, etc., and the file-set check rejects them. Without that flag every replay aborts with a checksum mismatch (this is a separate, known annoyance).

## Run pattern (3 commands per test)

```bash
# 1. record
RETRACE_CONFIG=debug python -m retracesoftware test.retrace -- python test.py

# 2. extract per-PID streams
python -m retracesoftware test.retrace --extract test.d

# 3. replay the root PID
ROOT_PID=$(ls -S test.d/*.bin | head -1 | xargs -n1 basename | sed 's/\.bin$//')
RETRACE_SKIP_CHECKSUMS=1 \
  python -m retracesoftware --recording test.d/${ROOT_PID}.bin
```

`flask_basic_test` is the exception — see its section.

---

## Failing tests in this snapshot (7)

| # | Test | Symptom (one line) |
|---|------|--------------------|
| 1 | `appnope_test` | replay aborts: `Checkpoint difference: <ResultMessage> was expecting type:CheckpointMessage` |
| 2 | `opentelemetry_test` | replay aborts after the test logically finishes: `RuntimeError: Could not read: 1 bytes from tracefile with timeout: 1000 milliseconds` (and without `RETRACE_SKIP_CHECKSUMS=1` it dies during checksum verify) |
| 3 | `threading_stress_test` | **regression vs yesterday** — replay aborts with the same `Checkpoint difference: <ResultMessage> was expecting type:CheckpointMessage` shape |
| 4 | `flask_basic_test` | **replay starts up as if it were a record** — Flask is bound, page is served live, then on Ctrl-C: `Checkpoint difference: socket._accept vs unbound lock-acquire lambda` |
| 5 | `asynclruio_test` | replay hangs the dispatcher then aborts: `RuntimeError: Dispatcher: too many threads waiting for item` followed by an asyncio `ValueError: Invalid file descriptor: -1` during teardown |
| 6 | `billiard_test` | record finishes, extract writes children but **never writes the root PID** (`zsh: no such file or directory: ./test.d/69854.bin`); same multiprocessing/fork blocker bucket as historical `appnope_test` |
| 7 | `memray_test` | replay of the **last** subprocess of the test crashes inside `psutil.Process()`: `psutil.NoSuchProcess: process PID not found (pid=61491)` |

The 5 the user is actively asking Nathan/AI to triage are **1, 2, 3, 4, 7**. Items 5 and 6 are included because they were also reproduced in this run and look distinct.

---

## Verbatim error excerpts (from this morning's run)

### 1. `appnope_test`

```
Error: Not a Python script: -c
ROOT_PID=56931
replay run: /Users/danielpatrascanu/cookbook/examples/invoice-parser/.venv-retrace-py3120-2026-04-30-1000/bin/python
            [-m retracesoftware --recording .../appnope_test/test.d/56931.bin]
            (cwd=.../appnope_test)
WARNING: checksum mismatch ignored (RETRACE_SKIP_CHECKSUMS set)
=== appnope_test ===
disabled app nap
Checkpoint difference: <retracesoftware.protocol.messages.ResultMessage object at 0x1069a6230>
                       was expecting type:retracesoftware.protocol.messages.CheckpointMessage
```

Recording succeeds and produces a `.bin`. The `Error: Not a Python script: -c` line is a separate
warning from the wrapper but not the cause of failure — replay then runs and aborts on the very first
checkpoint mismatch after the `disabled app nap` stdout.

### 2. `opentelemetry_test`

With `RETRACE_SKIP_CHECKSUMS=1` (normal replay path):

```
RuntimeError: Could not read: 1 bytes from tracefile with timeout: 1000 milliseconds
Test complete. All spans processed through fake exporter.
[EXPORT] Shutdown exporter
```

The interesting bit is the ordering: the test's own "Test complete." / "Shutdown exporter" lines are
printed **after** the RuntimeError, suggesting it's a teardown / cleanup-on-replay issue (last bytes
of the trace never get drained or are drained twice).

Without `RETRACE_SKIP_CHECKSUMS=1` the same recording dies earlier:

```
replay run: /Users/danielpatrascanu/cookbook/examples/invoice-parser/.venv-retrace-py3120-2026-04-30-1000/bin/python
            [-m retracesoftware --recording .../opentelemetry_test/test.d/58570.bin]
            (cwd=.../opentelemetry_test)
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File ".../retracesoftware/src/retracesoftware/__main__.py", line 517, in <module>
    main()
  File ".../retracesoftware/src/retracesoftware/__main__.py", line 514, in main
    replay(args)
  ...  (checksum mismatch, AGENTS.md/DESIGN.md bundled in the wheel)
```

### 3. `threading_stress_test` *(regression — was passing yesterday)*

```
replay aborts with
Checkpoint difference: <ResultMessage> was expecting type:CheckpointMessage
```

Same fingerprint as `appnope_test`. Worth bisecting between yesterday's HEAD and `7e01876`
(commit message: "Trace Thread.start at child run boundary") because that touches threading
boundaries directly.

### 4. `flask_basic_test`

This is the simple deterministic Flask app we added under
`dockertests/tests/flask_basic_test/`. The reproduction is **manual**:

```bash
# record (interactive — you press Ctrl-C after a refresh or two)
RETRACE_CONFIG=debug python -m retracesoftware test.retrace -- python test.py
# extract
python -m retracesoftware test.retrace --extract test.d
# replay (also interactive — should NOT serve live traffic, just replay the recorded request loop)
ROOT_PID=$(ls -S test.d/*.bin | head -1 | xargs -n1 basename | sed 's/\.bin$//')
RETRACE_SKIP_CHECKSUMS=1 \
  python -m retracesoftware --recording test.d/${ROOT_PID}.bin
```

What we see on **replay**:

```
Flask basic test running.
Open: http://127.0.0.1:5000/
Refresh the page a few times, then press Ctrl-C here.
============================================================
 * Serving Flask app 'test'
 * Debug mode: off
WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit
127.0.0.1 - - [30/Apr/2026 10:09:51] "GET / HTTP/1.1" 200 -
^CCheckpoint difference: {'function': wrapped_function:socket._accept -> method_descriptor:socket._accept,
                          'args': (<socket.socket object at 0x106c5cd70>),
                          'kwargs': {}}
                was expecting
                         {'function': wrapped_function:retracesoftware.proxy.system._ext_proxytype_from_spec
                                     .<locals>.unbound_function.<locals>.<lambda>
                                     -> function:retracesoftware.proxy.system._ext_proxytype_from_spec
                                     .<locals>.unbound_function.<locals>.<lambda>,
                          'args': (<_thread.lock object at 0x106c81250>),
                          'kwargs': {}}
```

Two distinct things wrong:

1. The replayer is **actually serving live HTTP** (the welcome banner, the `127.0.0.1 - - "GET /"` access log line, an interactive port). On replay these should not happen — the proxy layer should be intercepting `socket.accept` / the WSGI loop and feeding the recorded request back deterministically.
2. Once we Ctrl-C, the checkpoint diff shows the replayer expected a `_thread.lock` acquisition (`_ext_proxytype_from_spec` lambda) but got the real `socket._accept`. So the real-vs-recorded boundary has slipped one event — recording captured a lock acquire next, replay reached an `accept()` because it was running live.

Strongly suggests `socket.accept` (or the `socket.socket` object that `flask`/`werkzeug` builds during `app.run`) is no longer being proxied on replay.

### 5. `asynclruio_test`

```
_, item = self._dispatcher.next(
       ^^^^^^^^^^^^^^^^^^^^^^
RuntimeError: Dispatcher: too many threads waiting for item
Exception ignored in: <function BaseEventLoop.__del__ at 0x10612fb00>
Traceback (most recent call last):
 File "/Users/danielpatrascanu/.pyenv/versions/3.12.0/lib/python3.12/asyncio/base_events.py", line 705, in __del__
 File "/Users/danielpatrascanu/.pyenv/versions/3.12.0/lib/python3.12/asyncio/unix_events.py", line 68, in close
 File "/Users/danielpatrascanu/.pyenv/versions/3.12.0/lib/python3.12/asyncio/selector_events.py", line 104, in close
 File "/Users/danielpatrascanu/.pyenv/versions/3.12.0/lib/python3.12/asyncio/selector_events.py", line 111, in _close_self_pipe
 File "/Users/danielpatrascanu/.pyenv/versions/3.12.0/lib/python3.12/asyncio/selector_events.py", line 294, in _remove_reader
 File "/Users/danielpatrascanu/.pyenv/versions/3.12.0/lib/python3.12/selectors.py", line 190, in get_key
 File "/Users/danielpatrascanu/.pyenv/versions/3.12.0/lib/python3.12/selectors.py", line 71, in __getitem__
 File "/Users/danielpatrascanu/.pyenv/versions/3.12.0/lib/python3.12/selectors.py", line 225, in _fileobj_lookup
 File "/Users/danielpatrascanu/.pyenv/versions/3.12.0/lib/python3.12/selectors.py", line 42, in _fileobj_to_fd
ValueError: Invalid file descriptor: -1
replay: replay exited: exit status 1
```

Two-stage failure: the dispatcher complains about too many waiters (looks like more replay-side
threads than recorded waiters), then a follow-up asyncio teardown crash on a closed fd. The second
trace is almost certainly a side-effect of the first.

### 6. `billiard_test`

```
Retrace(69854) - ObjectWriter[1661] -- CHECKPOINT
Retrace(69854) - ObjectWriter[1662] -- <dict at 0x1049b5080>
Retrace(69854) - ObjectWriter[1663] -- RESULT
Retrace(69854) - ObjectWriter[1664] -- 69854
Process-2 has completed with exitcode 0.
wrote test.d/index.json
wrote test.d/69861.bin
wrote test.d/69862.bin
extracted 2 PidFile(s) to ./test.d
ROOT_PID=69854
zsh: no such file or directory: ./test.d/69854.bin
```

`extract` only emits the **child** PID files (`69861`, `69862`), never the **root** (`69854`).
This is the same fork/multiprocessing bucket that has been the long-standing blocker.

### 7. `memray_test` (replay of the *last* subprocess)

```
Testing memory usage reporting...
Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File ".../retracesoftware/src/retracesoftware/__main__.py", line 517, in <module>
    main()
  File ".../retracesoftware/src/retracesoftware/__main__.py", line 514, in main
    replay(args)
  File ".../retracesoftware/src/retracesoftware/__main__.py", line 331, in replay
    system.run(run_python_command, header["argv"])
  File ".../retracesoftware/src/retracesoftware/proxy/system.py", line 547, in run
    return run_internal(*args, **kwargs)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File ".../retracesoftware/src/retracesoftware/run.py", line 88, in run_python_command
    runpy.run_path(script_path, run_name="__main__")
  File "<frozen runpy>", line 286, in run_path
  File "<frozen runpy>", line 98, in _run_module_code
  File "<frozen runpy>", line 88, in _run_code
  File ".../retracesoftware/src/retracesoftware/install/importhook.py", line 88, in _exec_and_patch_entry
    _orig_exec(source, globals, locals)
  File "test.py", line 72, in <module>
    test_memray_profiling()
  File "test.py", line 61, in test_memray_profiling
    process = psutil.Process()
              ^^^^^^^^^^^^^^^^
  File ".../site-packages/psutil/__init__.py", line 314, in __init__
    self._init(pid)
  File ".../site-packages/psutil/__init__.py", line 360, in _init
    raise NoSuchProcess(pid, msg=msg) from None
psutil.NoSuchProcess: process PID not found (pid=61491)
replay: replay exited: exit status 1
```

`psutil.Process()` with no args reads `os.getpid()` then `/proc/<pid>` (or the macOS equivalent).
On replay we're feeding it the **recorded** PID (`61491`) which doesn't exist in the replay process.
Strongly looks like `psutil.Process(None)` / `os.getpid()` path is not being intercepted to return
the recorded value, or `psutil`'s open-of-process-info isn't being proxied.

---

## Suggested triage order for Nathan / Nathan's AI

1. **`flask_basic_test`** — most diagnostic. It clearly shows `socket.accept` running live on replay. Whatever made the proxy fall through here probably also explains some of the other "checkpoint diff" failures.
2. **`appnope_test` & `threading_stress_test`** — same `<ResultMessage> was expecting type:CheckpointMessage` fingerprint; almost certainly the same bug. `threading_stress_test` is fresh regression at `7e01876` ("Trace Thread.start at child run boundary"), so a bisect there should be cheap.
3. **`opentelemetry_test`** — the "test prints completion line *after* the RuntimeError" is interesting; smells like a final-flush race in `tracefile`/dispatcher shutdown.
4. **`memray_test`** — focused issue: `psutil.Process()` PID resolution on replay.
5. **`asynclruio_test`** — likely a downstream symptom; revisit after #1/#2 land.
6. **`billiard_test`** — multiprocessing/fork bucket; deferred unless someone is actively on the multiprocessing work.

## Cross-cutting issue

Every replay above needs `RETRACE_SKIP_CHECKSUMS=1` because the built wheel includes
`AGENTS.md` / `DESIGN.md` files under `retracesoftware/proxy/`, `retracesoftware/install/`, etc., which
makes the recorded module-file checksum disagree with the replay environment. Either exclude
those files from the wheel or relax the checksum comparison for non-`.py`/`.so` files.
