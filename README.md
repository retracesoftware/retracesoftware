# Retrace

**Record production Python. Debug it backwards.**

Retrace is the first reverse debugger designed for production CPython
applications. Record a failing execution once, replay it locally and
deterministically in VS Code, and step backwards from the crash to the cause.

![Retrace reverse-step in VS Code](docs/assets/reverse-step.gif)

Retrace records the boundary between your Python code and nondeterministic
outside behavior (network, database, filesystem, time, randomness, model
output, subprocess behavior), then replays the execution locally. Your Python
code runs again, but recorded external calls return the values from the trace
instead of touching the live world.

Retrace is useful when a bug depends on timing, network responses, filesystem
state, model output, subprocess behavior, random values, clocks, or anything
else that is painful to reproduce from logs alone.

This is an open-source preview. See [Compatibility](#compatibility) for current
scope.

## What Retrace Is Not

Retrace is not an APM tool. It does not sample traces or aggregate metrics
across requests.

It is not a logging library. You do not decide in advance which variables
might matter.

It is not rr for Python. Retrace does not record an entire machine process at
the syscall level. It records the boundary between your Python code and the
outside world, at Python semantics.

## Performance

Retrace records at the Python/external boundary, not at the instruction
level. For I/O-bound workloads such as Flask and Django services handling
external API calls and database queries, recording overhead on our reference
benchmarks is below 0.1% of request latency. CPU-bound workloads see higher
overhead because crossing the boundary more frequently increases the number
of recorded events; pure-Python computation between boundary calls runs at
full speed.

Full benchmark methodology and reproducible results:
[docs/performance.md](docs/performance.md).

## Compatibility

Retrace works with the mainstream Python web and async stack: Flask, Django,
FastAPI, Starlette, requests, httpx, aiohttp, asyncio, AnyIO, NumPy, Pandas,
Pydantic, psycopg2, plus the standard-library determinism boundaries
(threading, sockets, subprocess, ssl, sqlite3).

See [COMPATIBILITY.md](./COMPATIBILITY.md) for the full matrix, what "tested"
means, and known gaps (SQLAlchemy, Redis, boto3, Celery).

**Python versions:** [TODO]
**Operating systems:** [TODO]

## Quick Start

The fastest way to try Retrace is the included Flask demo.

```bash
git clone https://github.com/retracesoftware/retracesoftware.git
cd retracesoftware/quickstart

# Retrace's replay tool is written in Go; verify Go 1.25+ is on your PATH
go version

python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install retracesoftware
python -m retracesoftware install
python -m pip install -r requirements.txt

RETRACE_RECORDING=recordings/flask.retrace python examples/flask_demo.py
code .
```

In VS Code:

1. Install the `Retrace Debug Extension` from the Marketplace.
2. Open the Retrace sidebar.
3. Choose `Open Recording...`.
4. Select `recordings/flask.retrace`.
5. Open `examples/flask_demo.py`.
6. Set a breakpoint inside a route handler or inside `main()`.
7. Start replay from the Retrace view.

The replay should stop at your breakpoint inside the recorded execution. You can
inspect variables, continue, step forward, and step backward without running
the Flask demo live again.

For the full walkthrough, see [quickstart/README.md](https://github.com/retracesoftware/retracesoftware/blob/main/quickstart/README.md).

## Requirements

- See [Compatibility](#compatibility) for Python and OS support.
- `pip`
- Go 1.25 or newer on `PATH` (Retrace installs with `pip`, but replay
  extraction and VS Code replay/debugging use Retrace's Go replay tool).

On macOS with Homebrew:

```bash
brew install go
```

On Linux, install Go 1.25 or newer from your distro packages or from
[go.dev/dl](https://go.dev/dl/).

## How Recording Works

Install the package:

```bash
python -m pip install retracesoftware
```

Enable the auto-recording hook in the active virtual environment:

```bash
python -m retracesoftware install
```

That installs a `.pth` file into the environment. Fresh Python processes in
that environment import Retrace at startup, but they only record when you set
a Retrace environment variable.

Record an ordinary Python file:

```bash
RETRACE_RECORDING=recordings/run.retrace python my_script.py
```

Retrace creates the parent directory if needed and writes an executable
`.retrace` file. The recording stores the command, working directory,
environment, Python version, Retrace checksums, and recorded boundary calls.

You can also record without the `.pth` hook:

```bash
python -m retracesoftware --recording recordings/run.retrace -- my_script.py
```

For module-based apps and tools, put `RETRACE_RECORDING=...` before the same
Python command you would normally run:

```bash
RETRACE_RECORDING=recordings/cli.retrace python -m your_package.cli --input examples/input.json
RETRACE_RECORDING=recordings/tests.retrace python -m pytest tests/
RETRACE_RECORDING=recordings/debug.retrace python -c "import random; print(random.random())"
```

For more examples, see [docs/getting-started/recording-python-commands.md](https://github.com/retracesoftware/retracesoftware/blob/main/docs/getting-started/recording-python-commands.md).

## Replay And Debug In VS Code

Open the same folder that contains your source and `.retrace` file:

```bash
code .
```

Then open the recording from the Retrace sidebar or right-click the
`.retrace` file and choose `Open as Retrace Recording`.

The extension reads the replay binary path embedded in the `.retrace`
shebang, indexes the recorded process tree, and launches replay debugging
through the Go replay tool.

Set breakpoints in the recorded Python code and start replay. The debugger
runs the recorded execution, not a live process.

See [docs/getting-started/vscode-extension.md](https://github.com/retracesoftware/retracesoftware/blob/main/docs/getting-started/vscode-extension.md).

## Terminal Replay

Extract the recording:

```bash
./recordings/run.retrace --extract
```

That creates:

```
recordings/run.d/index.json
recordings/run.d/<PID>.bin
```

Find the root process:

```bash
ROOT_PID=$(python -m retracesoftware --recording recordings/run.retrace --list_pids | head -1)
```

Replay it:

```bash
./recordings/run.d/${ROOT_PID}.bin
```

## Documentation

- [Documentation index](https://github.com/retracesoftware/retracesoftware/blob/main/docs/README.md)
- [Compatibility](https://github.com/retracesoftware/retracesoftware/blob/main/COMPATIBILITY.md)
- [Getting started](https://github.com/retracesoftware/retracesoftware/blob/main/docs/getting-started/README.md)
- [Installation](https://github.com/retracesoftware/retracesoftware/blob/main/docs/getting-started/installation.md)
- [Quickstart](https://github.com/retracesoftware/retracesoftware/blob/main/quickstart/README.md)
- [Recording Python commands](https://github.com/retracesoftware/retracesoftware/blob/main/docs/getting-started/recording-python-commands.md)
- [VS Code extension](https://github.com/retracesoftware/retracesoftware/blob/main/docs/getting-started/vscode-extension.md)
- [Performance and benchmarks](https://github.com/retracesoftware/retracesoftware/blob/main/docs/performance.md)
- [Reference](https://github.com/retracesoftware/retracesoftware/blob/main/docs/reference/README.md)
- [CLI reference](https://github.com/retracesoftware/retracesoftware/blob/main/docs/reference/cli.md)
- [Environment variables](https://github.com/retracesoftware/retracesoftware/blob/main/docs/reference/environment-variables.md)
- [Recording files](https://github.com/retracesoftware/retracesoftware/blob/main/docs/reference/recording-files.md)
- [Troubleshooting](https://github.com/retracesoftware/retracesoftware/blob/main/docs/troubleshooting.md)
- [Internals](https://github.com/retracesoftware/retracesoftware/blob/main/docs/internals/README.md)
- [Architecture](https://github.com/retracesoftware/retracesoftware/blob/main/docs/internals/architecture.md)

## Development From Source

Install from this checkout:

```bash
python -m pip install --upgrade pip wheel
python -m pip install "meson>=1.3" "meson-python>=0.18.0" "setuptools_scm>=8.0.4" ninja
python -m pip install --no-build-isolation -e .
```

The package includes Python code, native extensions built by Meson, module
interception config, and the Go replay tooling used for extraction, terminal
replay, and VS Code replay/debugging. Supported wheels include the replay
binary; source/development installs can build it lazily if it is missing,
which is why Go is required on `PATH`.

Run Python tests:

```bash
python -m pytest tests/ -v --tb=short
```

Run Go tests:

```bash
cd go
go test ./...
```

## Repository Layout

- `quickstart/` first-run demo and public quickstart flow
- `src/retracesoftware/__main__.py` CLI record/replay entrypoint
- `src/retracesoftware/autoenable.py` `.pth` startup hook implementation
- `src/retracesoftware/tape.py` recording file setup, checksums, and tape I/O
- `src/retracesoftware/install/` runtime patching and import hooks
- `src/retracesoftware/proxy/` record/replay boundary semantics
- `src/retracesoftware/modules/` stdlib and third-party interception config
- `src/retracesoftware/stream/` and `cpp/stream/` trace serialization
- `src/retracesoftware/dap/` Python debugger protocol pieces
- `go/` replay extraction, indexing, and debug adapter tooling
- `vscode/` VS Code extension
- `tests/` and `dockertests/` unit, replay, and scenario tests
- `docs/` user and maintainer documentation

## Built By

Retrace is built by [Retrace Software](https://retracesoftware.com) in London.
Backed by [Preston-Werner Ventures](https://preston-werner.com). Advised by
[Yury Selivanov](https://github.com/1st1), creator of asyncio and uvloop, PSF
Fellow.

The patented value-level provenance engine that sits on top of record-replay
(granted UK, US, EU patents) is being prepared for separate release.

## License

Apache-2.0
