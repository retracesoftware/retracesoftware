# Test Harnesses

Retrace has two harnesses for scenario-level validation:

- `dockertests/`: end-to-end Docker scenarios that install the current
  checkout, run a real `test.py`, record it, extract the recording, and replay
  the root PidFile.
- `tests/test_dockertests_inprocess.py`: pytest scenarios that reuse selected
  dockertest bodies through `tests.runner.Runner` and an in-memory tape.

They are intentionally separate. The Docker harness tests the public
record/extract/replay workflow. The in-process harness tests the proxy
record/replay kernel without Docker, the CLI, a `.retrace` file, or the Go
replay binary.

## Docker Harness

Use the Docker harness when the scenario needs one of these:

- the `.pth` auto-enable startup path
- `python -m retracesoftware --recording ...`
- extraction and root PidFile replay
- real subprocesses or multiprocessing
- a long-running server plus a separate client
- Docker services such as Postgres
- network isolation during replay
- packaging/install behavior in a clean container

Run one scenario:

```bash
cd retracesoftware/dockertests
python run.py simple_test
```

Run one scenario directly through the shell wrapper:

```bash
cd retracesoftware/dockertests
./runtest.sh simple_test
```

Choose the Retrace config:

```bash
python run.py simple_test --retrace-config normal
python run.py simple_test --retrace-config debug
./runtest.sh simple_test --retrace-config debug
```

`normal` maps to the bundled `release` config. `debug` maps to
`RETRACE_CONFIG=debug`.

The default Docker flow is:

```text
install -> dryrun -> record -> replay -> cleanup
```

Server scenarios, identified by a `client.py` next to `test.py`, use:

```text
install -> server-dryrun -> dryrun -> server-record -> record -> replay -> cleanup
```

The default record path uses the same `.pth` workflow documented for users:

```bash
python -m retracesoftware install
RETRACE_RECORDING=/recording/test.retrace python /app/test/test.py
```

The default replay path extracts and runs the root PidFile:

```bash
/recording/test.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording /recording/test.retrace --list_pids | head -n 1)
/recording/test.d/${ROOT_PID}.bin
```

The replay phase runs with Docker networking disabled. If replay passes, the
scenario did not silently satisfy recorded network behavior by touching live
services.

More details: [`dockertests/README.md`](../../dockertests/README.md).

## In-Process Pytest Harness

Use `tests/test_dockertests_inprocess.py` when the scenario can honestly be a
finite same-process Python function. The harness records and replays the
function through `tests.runner.Runner` using `IOMemoryTape`.

Run all in-process scenarios:

```bash
cd retracesoftware
python -m pytest -q tests/test_dockertests_inprocess.py
```

Choose the Retrace config:

```bash
python -m pytest -q tests/test_dockertests_inprocess.py --retrace-config normal
python -m pytest -q tests/test_dockertests_inprocess.py --retrace-config debug
```

Run one scenario:

```bash
python -m pytest -q 'tests/test_dockertests_inprocess.py::test_dockertest_inprocess[simple_test]'
```

Each scenario runs in a child Python process for isolation. Inside that child,
record/replay is still in-process Runner behavior:

- no Docker
- no `.pth` auto-enable
- no `python -m retracesoftware --recording`
- no `.retrace` file
- no Go replay binary

This isolation prevents a failed patch/replay attempt from poisoning later
pytest parameters while keeping failures close to the proxy/runtime layer.

More details: [`tests/README.md`](../../tests/README.md).

## What Belongs In Each Harness

Put a scenario in the in-process harness when:

- it is a finite Python function
- it has no real child process boundary
- it does not require a live server/client split
- it does not need Docker services
- it does not specifically test CLI, `.pth`, extraction, or PidFile replay

Keep a scenario in Docker when:

- translating it to Runner would remove the behavior being tested
- the scenario exists to reproduce a user-visible record/extract/replay issue
- the scenario depends on process lifecycle, network lifecycle, or container
  services

Do not add a third local CLI harness. If a manual local command is useful for
debugging, keep it in the issue or scenario README as a repro command, not as a
new runner.

## Current In-Process Coverage

The current in-process harness covers:

```text
simple_test
time1
datetime_test
numpy_test
pydantic_test
cryptography_test
lz4_test
bytecode_test
filelock_test
arrow_test
jsonschema_test
black_test
asyncio_test
pandas_test
asgiref_test
asynclruio_test
requests_test
threading_stress_test
pyspy_test
coreapi_test
grpc_test
cachecontrol_test
memray_test
```

Good next candidates from `dockertests/tests/`:

```text
aiorwlock_test
aiosignal_test
anyio_test
apischema_test
appdirs_test
astroid_test
asttokens_test
async_lru_random_test
asynclru_test
babel_test
backoff_test
click_test
dateutil_test
execnet_test
fastapi_endpoints_test
fastapi_test
flask_simple_test
fsspec_test
httpcore_test
opencensus_test
packaging_test
pandas_dataframe_test
rich_test
scipy_test
```

Keep these Docker-first unless there is a narrow reason to model part of them
in Runner:

```text
appnope_test
appnope_pth_autoenable_test
billiard_test
datasette_server_test
flask_basic_test
flask_server_test
flask_test
flight_search_relative_autoenable_test
http_perf_test
http_perf_slow_test
llama_cpp_model_boundary_test
postgress_test
psycopg2_test
subprocess_terminate_wait_timeout_test
```

`aiohttp_cors_test`, `opentelemetry_test`, and `pyopenssl_test` are possible
Runner candidates only with care. They involve local server/socket/background
worker behavior, so a careless port would test a different program.
