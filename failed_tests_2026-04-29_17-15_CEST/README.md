# Failing dockertests — snapshot 2026-04-29 17:15 CEST

Five dockertest scenarios are failing under HEAD `ca7eb3c Optimize install
wrappers and dispatcher waits` on branch
`backup/wip-2026-04-04-system-io-save`. This folder is a frozen mirror of
those 5 test bodies plus a brief per-test error report so Nathan's AI can
summarise / triage / fix them without chasing the live tree.

The 5 failing tests in this snapshot:

- `appnope_test`
- `opentelemetry_test`
- `threading_stress_test`
- `memray_test`
- `flask_basic_test` (the simple interactive Flask test added today)

Authoritative source for each: `retracesoftware/dockertests/tests/<name>/test.py`.

---

## Environment

- Python: 3.12.0 (pyenv)
- venv: `/Users/danielpatrascanu/cookbook/examples/invoice-parser/.venv-retrace-py3120-2026-04-29-1700`
- retracesoftware: `0.1.15.dev100+gca7eb3c50.d20260429`, built from current HEAD
- Go replay binary: shipped at `<site-packages>/retracesoftware/replay/replay`
- Replay step requires `RETRACE_SKIP_CHECKSUMS=1` because the wheel now ships
  `AGENTS.md` / `DESIGN.md` files inside `retracesoftware/proxy/`,
  `retracesoftware/install/`, etc., and the replayer's module-file checksum
  check flags those as a manifest mismatch.

---

## Bash flow used per test

```bash
source /Users/danielpatrascanu/cookbook/examples/invoice-parser/.venv-retrace-py3120-2026-04-29-1700/bin/activate
cd /Users/danielpatrascanu/cookbook/examples/invoice-parser/retracesoftware/dockertests/tests/<NAME>
[ -f requirements.txt ] && python -m pip install -q -r requirements.txt
rm -f test.retrace; rm -rf test.d
RETRACE_CONFIG=debug python test.py
./test.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording test.retrace --list_pids | head -1)
RETRACE_SKIP_CHECKSUMS=1 ./test.d/${ROOT_PID}.bin
```

`flask_basic_test` is interactive: after `RETRACE_CONFIG=debug python test.py`,
open `http://127.0.0.1:5000/` in a browser, refresh a few times, then Ctrl-C
the terminal before running `--extract` and replay.

---

## Per-test errors (verbatim, Daniel 2026-04-29 17:15 CEST)

### `appnope_test`

```
appnope replay exit status code 1 — finishes but doesn't cleanup properly
```

Note: this test forks a child via `multiprocessing`. The cleanup-1 surface
is new in this commit; on the previous snapshot the same test failed earlier
with `posix.close` vs `posix._exit` checkpoint divergence.

### `opentelemetry_test`

```
RuntimeError: Could not read: 1 bytes from tracefile with timeout: 1000 milliseconds
Test complete. All spans processed through fake exporter.
```

Note: the test's expected output is produced — the failure is in the
`OtelBatchSpanRecordProcessor` worker thread blocking on a tape-read past
the end of the recording during shutdown.

### `threading_stress_test`

```
threading_stress_test: replay exit status 1
```

Stack not captured by Daniel — re-running from the bash flow above will
produce one.

### `memray_test`

```
File "/Users/danielpatrascanu/cookbook/examples/invoice-parser/.venv-retrace-py3120-2026-04-29-1700/lib/python3.12/site-packages/psutil/__init__.py", line 314, in __init__
    self._init(pid)
File "/Users/danielpatrascanu/cookbook/examples/invoice-parser/.venv-retrace-py3120-2026-04-29-1700/lib/python3.12/site-packages/psutil/__init__.py", line 360, in _init
    raise NoSuchProcess(pid, msg=msg) from None
psutil.NoSuchProcess: process PID not found (pid=28481)
replay: replay exited: exit status 1
```

Note: `psutil.Process()` records the live PID at record time; on replay the
recorded PID no longer exists in the OS process table, so psutil raises.
Either materialize `psutil.Process` more aggressively in retrace, or treat
this test as inherently non-replayable.

### `flask_basic_test`

A small interactive Flask app added today (`dockertests/tests/flask_basic_test/test.py`).
Records cleanly. Replay fails with:

```
Checkpoint difference:
  {'function': wrapped_function:socket._accept -> method_descriptor:socket._accept,
   'args': (<socket.socket object at 0x10535c9f0>),
   'kwargs': {}}
  was expecting
  {'function': wrapped_function:retracesoftware.proxy.system._ext_proxytype_from_spec.<locals>.unbound_function.<locals>.<lambda>
               -> function:retracesoftware.proxy.system._ext_proxytype_from_spec.<locals>.unbound_function.<locals>.<lambda>,
   'args': (<_thread.lock object at 0x1053806d0>),
   'kwargs': {}}
```

Daniel additionally observed: "if i start the replay it starts as a
recording somehow":

```
* Debug mode: off
WARNING: This is a development server. Do not use it in a production deployment.
Use a production WSGI server instead.
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit
127.0.0.1 - - [29/Apr/2026 17:13:27] "GET / HTTP/1.1" 200 -
127.0.0.1 - - [29/Apr/2026 17:13:27] "GET /favicon.ico HTTP/1.1" 404 -
```

Replay should not be running a live Flask development server and accepting
real HTTP requests on port 5000 — this strongly suggests the replayer is
delegating to live `socket._accept` (the very mismatch shown in the
checkpoint diff above). Net effect: socket I/O is happening for real
instead of being served from the recorded tape.

---

## Triage hint

`flask_basic_test` is the cleanest fingerprint of new replayer behaviour in
this commit window: replay is firing live `socket._accept` where the recorded
tape expected a lock-acquisition lambda from
`system._ext_proxytype_from_spec`. That's not a tape-vs-tape mismatch —
that's the replayer falling through to a live extern call. Almost certainly
downstream of one of the recently landed commits touching the gateway /
dispatcher / install wrappers.

Provenance: `ca7eb3c` HEAD on `backup/wip-2026-04-04-system-io-save`, in
sync with origin. All `test.py` files in this folder are byte-identical
copies of the live committed source. The live tree is the source of truth.
