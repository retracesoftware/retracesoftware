# Docker Tests

Docker scenario tests exercise Retrace against real libraries and small apps in
isolated containers. The harness is intentionally closer to how users run
Retrace than to a unit-test fixture: it installs the current checkout, runs the
program once normally, records it, extracts the recording, and replays the root
PidFile with networking disabled.

## Quick Start

```bash
# Run all non-performance tests
python run.py

# Run one test
python run.py simple_test

# List available tests
python run.py --list

# Clean package caches and stale harness containers before running
python run.py --clean simple_test
```

Default behavior:

- Docker image: `python:3.12`
- Record mode: `.pth` auto-enable flow
- Replay mode: extracted root PidFile
- Retrace config: `normal` (`release` preset)
- Successful recordings are cleaned unless `--keep-recording` is passed

## Pipeline

Script tests run:

```text
install -> dryrun -> record -> replay -> cleanup
```

Server tests, identified by a `client.py` next to `test.py`, run:

```text
install -> server-dryrun -> dryrun -> server-record -> record -> replay -> cleanup
```

`dryrun` proves the app works without Retrace. `record` uses the same `.pth`
auto-enable path documented for users:

```bash
python -m retracesoftware install
RETRACE_RECORDING=/recording/test.retrace python /app/test/test.py
```

`replay` extracts and runs the recorded root process:

```bash
/recording/test.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording /recording/test.retrace --list_pids | head -n 1)
/recording/test.d/${ROOT_PID}.bin
```

Replay runs with `network_mode: none`, so a passing replay cannot be silently
touching live network services from the recording phase.

## Commands

```bash
# Run one test with the default modern flow
./runtest.sh simple_test

# Keep /recording/test.retrace and /recording/test.d/ after success
./runtest.sh simple_test --keep-recording

# Run the same flow with Retrace's debug preset
./runtest.sh simple_test --retrace-config debug

# Compare against the legacy direct/unframed Python replay path
./runtest.sh simple_test --record-mode direct --replay-mode recording

# Use a different Python image
./runtest.sh simple_test --image python:3.11-slim

# Increase one-test timeout
./runtest.sh simple_test --timeout 1200

# Run all tests tagged with db, excluding perf tests unless explicitly requested
python run.py --tags db

# Run through the Python wrapper with the debug preset
python run.py simple_test --retrace-config debug

# Include performance tests
python run.py --include-perf
```

`normal` maps to Retrace's bundled `release` config. Use
`--retrace-config debug` when you want the richer checkpoint/protocol path used
for debugging replay desyncs.

## Test Layout

Each test directory is mounted at `/app/test` and usually contains:

```text
dockertests/tests/my_test/
  test.py
  requirements.txt        # optional
  docker-compose.yml      # optional override
  tags                    # optional, one tag per line
  client.py               # optional; marks the test as a server scenario
```

If `docker-compose.yml` is missing, the base workflow is enough. If it is
present, Docker Compose merges it on top of the base workflow so the test can
add services such as Postgres, custom health checks, or test-specific
environment variables.

## Creating A Script Test

```bash
mkdir -p dockertests/tests/my_test
```

Create `dockertests/tests/my_test/test.py`:

```python
import random
from datetime import datetime


def main():
    print("now", datetime.now())
    print("random", random.random())


if __name__ == "__main__":
    main()
```

Add dependencies only when needed:

```bash
printf "requests\n" > dockertests/tests/my_test/requirements.txt
```

Run it:

```bash
cd dockertests
python run.py my_test --keep-recording
```

## Creating A Server Test

A server test has a long-running `test.py` and a foreground `client.py`.

```text
dockertests/tests/flask_like_test/
  test.py       # starts the server under Retrace during record
  client.py     # sends requests during dryrun/record
  requirements.txt
```

The harness records only the server process. During replay, the server code
re-executes from the recording with no live client and no live network.

Use `docker-compose.yml` only for overrides, for example:

```yaml
services:
  server-dryrun:
    environment:
      DB_FILE: /tmp/app.db

  server-record:
    environment:
      DB_FILE: /tmp/app.db

  replay:
    environment:
      DB_FILE: /tmp/app.db

  dryrun:
    environment:
      FLASK_URL: http://server-dryrun:5000

  record:
    environment:
      FLASK_URL: http://server-record:5000
```

The base server workflow supplies package mounts, recording mounts, health
readiness waits, record/replay commands, server shutdown, and cleanup.

Do not add continuous Docker healthchecks to `server-record`: healthchecks are
real HTTP traffic into the recorded process and will become part of the trace.
The harness already waits for the TCP port once before running `client.py`.

## Dependency Install

`install.sh` installs into a per-test/per-image target under `.cache/packages`.
It installs:

1. the test's `requirements.txt`, if present
2. `dockertests/base-requirements.txt`
3. the current Retrace checkout mounted at `/app/repo`

That means docker tests validate the current source tree, not the published
PyPI package.

The current checkout build needs native compilation and Go 1.25+. The harness
installs missing build tools inside the test container, including a Go 1.25
toolchain when the base image does not provide one.

Use `python run.py --clean ...` to remove package caches, stale per-test
recordings, and stale Compose objects for projects named `retracetest_*`.

## Record And Replay Modes

The default mode is the product path:

```text
--record-mode pth --replay-mode pidfile
```

This creates `/recording/test.retrace`, extracts `/recording/test.d/`, and
executes the recorded root PidFile.

The legacy mode is still available for isolating older replay bugs:

```text
--record-mode direct --replay-mode recording
```

That writes an unframed recording and replays it directly through
`python -m retracesoftware --recording`.

## Troubleshooting

**Docker is not available**

Start Docker Desktop or use local manual commands for the specific scenario.

**Replay fails**

Run the test again with:

```bash
./runtest.sh <test_name> --keep-recording
```

Then inspect:

```text
dockertests/tests/<test_name>/recording/test.retrace
dockertests/tests/<test_name>/recording/test.d/
```

**Need to keep the failing recording**

Run:

```bash
./runtest.sh <test_name> --keep-recording
```

Then inspect:

```text
dockertests/tests/<test_name>/recording/test.retrace
dockertests/tests/<test_name>/recording/test.d/
```

**Packages look stale**

```bash
python run.py --clean <test_name>
```

**Need lower-level logs**

Use:

```bash
RETRACE_STACKTRACES=1 ./runtest.sh <test_name> --keep-recording
```

On failure, `runtest.sh` prints the failed phase and the relevant Compose logs.
