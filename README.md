# Retrace

Turn failed Python tests and CI runs into replayable debug sessions.

Retrace records a Python execution as a `.retrace` artifact. When pytest or CI fails, open the artifact locally in VS Code, replay the same failed run, and step backwards from the failure to inspect the runtime state that caused it.

Start with tests today. Use the same recording model for Python apps and production crashes when the trust is there.

> Demo: a failed pytest run replayed in VS Code, stepping backwards from the assertion failure to the runtime state that caused it.

Start here: [Replay a failed pytest run with the full quickstart](quickstart/README.md).

---

## Why Retrace

Most failed test and CI artifacts are logs, tracebacks, screenshots, or partial traces. They show symptoms. They do not preserve the execution.

Retrace preserves the failed run itself.

| Today | With Retrace |
| --- | --- |
| CI artifacts are logs and tracebacks | CI artifact is replayable |
| AI agents infer from partial context | AI agents get runtime evidence |
| Stack trace shows where it crashed | Replay shows what happened before |
| Logs show what you predicted would matter | Retrace preserves the failed execution |

The failed execution becomes something you can inspect, replay, and share.

---

## Quick Start: Replay a Failed pytest Run

The fastest way to understand Retrace is to run the controlled pytest demo:

```bash
git clone https://github.com/retracesoftware/retracesoftware.git
cd retracesoftware/quickstart

python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
python -m pip install retracesoftware
python -m pip install -r requirements.txt

mkdir -p recordings

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m retracesoftware --recording recordings/pytest.retrace -- -m pytest pytest_demo -q --tb=short
```

The demo intentionally fails and leaves behind:

```text
recordings/pytest.retrace
```

Open the same project in VS Code:

```bash
code .
```

Then:

1. Install the Retrace Debug Extension from the Marketplace.
2. Open the Retrace sidebar.
3. Choose `Open Recording...`.
4. Select `recordings/pytest.retrace`.
5. Open the failing test or the code under test.
6. Set a breakpoint near the failing assertion or exception.
7. Start replay from the Retrace view.

You can now debug the failed execution locally, inspect runtime state, and step backwards from the failure without re-running the test.

For the full walkthrough, including terminal replay and the CI-style replay bundle, see [quickstart/README.md](quickstart/README.md).

---

## CI Artifacts

Retrace works with ordinary CI artifact upload.

For example, in GitHub Actions:

```yaml
- name: Run pytest with Retrace
  run: |
    mkdir -p recordings
    set +e
    PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
      python -m retracesoftware --recording recordings/pytest.retrace -- -m pytest tests -q --tb=short
    PYTEST_STATUS=$?
    echo "$PYTEST_STATUS" > recordings/pytest-exit-code.txt
    exit "$PYTEST_STATUS"

- name: Upload Retrace recording
  if: failure()
  uses: actions/upload-artifact@v4
  with:
    name: retrace-failed-run
    path: |
      recordings/pytest.retrace
      recordings/pytest-exit-code.txt
    retention-days: 7
```

A failed CI run becomes a replayable artifact. Download it, open it locally in VS Code, and debug the same execution that failed in CI.

The first preview can upload the `.retrace` file directly. The longer-term CI shape is a replay bundle containing the trace, pytest output, manifest, dependency snapshot, and replay instructions.

No hosted trace service or GitHub App is required.

### Safety note

Treat `.retrace` files and replay bundles like debug dumps.

They may contain runtime values from the test execution. If your tests touch secrets, credentials, production-like data, customer-like fixtures, API responses, database responses, or sensitive files, handle the artifact carefully.

For early CI use, we recommend:

- do not upload `.retrace` artifacts from untrusted fork PRs by default;
- use short artifact retention;
- avoid capturing all environment variables;
- treat replay bundles like logs or crash dumps;
- start with private repositories or trusted branches;
- review your own security policy before sending replay bundles to any AI system.

---

## The Mental Model

A test fails.

Instead of rerunning and guessing, open the `.retrace` recording in VS Code. Replay the exact failed execution, inspect locals and call stack, and step backwards from the failing assertion to the state that caused it.

The recording is the failed run.

No reproduction steps. No retry loop. No guessing from logs.

---

## How It Works

### Run

Run pytest, CI, or your Python app with Retrace enabled.

### Record

Retrace records the execution into a `.retrace` artifact.

### Replay

Open the artifact locally in VS Code and debug the same execution.

### Step backwards

Move backwards from the failure and inspect the runtime state that caused it.

Under the hood, Retrace records the boundary between your Python code and the nondeterministic outside world: network responses, filesystem state, clocks, randomness, subprocess behavior, API calls, database calls, and other external effects.

During replay, your Python code runs for real, but those recorded boundary calls return their captured values instead of touching the live world. That makes replay deterministic and lets the debugger move through the original execution.

Threaded and parallel test support is being expanded as part of the pytest/CI workflow.

---

## Built For Humans And AI Agents

Retrace gives a developer, debugger, or AI coding agent the runtime ground truth of a failed execution, not just source code, logs, and a stack trace.

That matters because AI agents often infer what happened from partial context. A `.retrace` artifact gives them runtime evidence from the actual failed run.

Today, the primary replay/debugging workflow is VS Code. Next, Retrace will expose more of this runtime state through CLI and agent-facing workflows, so AI coding agents can inspect failed executions without relying on logs and tracebacks alone.

---

## Full Quickstart

The full quickstart includes a controlled failing pytest demo, terminal replay, VS Code replay debugging, and a CI-style replay bundle.

[Replay a failed pytest run with Retrace](quickstart/README.md)

---

## Production Is The Destination

The `.retrace` artifact from a failed test uses the same architecture as a production crash replay.

Start with tests today. Run the same tool against production when the trust is there.

Retrace records the boundary between your Python code and the outside world — databases, APIs, files, time, randomness, and other nondeterministic calls — then replays those results locally.

Recording is designed to be low-overhead enough for production use, while the first adoption path is deliberately safer: tests and CI.

---

## What Retrace Is Not

Retrace is not a logging library. You do not decide in advance which variables, branches, or errors might matter.

Retrace is not a metrics or tracing dashboard. It does not sample requests or aggregate performance data across your application.

Retrace is not `rr` for Python. It does not record an entire machine process at the syscall level. Instead, it records the boundary between your Python code and the outside world, then replays those interactions so the original execution can be debugged deterministically.

---

## Requirements

- CPython 3.11 or 3.12
- The controlled pytest quickstart uses Python 3.12
- macOS or Linux, 64-bit
- `pip`
- Go 1.25 or newer on `PATH`
- VS Code for the current replay/debugging workflow

Retrace installs with pip, but replay extraction and VS Code replay/debugging use Retrace's Go replay tool. If `go version` does not work, install Go before recording/replaying.

On macOS with Homebrew:

```bash
brew install go
```

On Linux, install Go 1.25 or newer from your distro packages or from:

```text
https://go.dev/dl/
```

See the [Retrace compatibility guide](COMPATIBILITY.md) for current platform details.

---

## Recording Python Commands

Install the package:

```bash
python -m pip install retracesoftware
```

Enable the auto-recording hook in the active virtual environment:

```bash
python -m retracesoftware install
```

That installs a `.pth` file into the environment. Fresh Python processes in that environment import Retrace at startup, but they only record when you set a Retrace environment variable.

Record an ordinary Python file:

```bash
RETRACE_RECORDING=recordings/run.retrace python my_script.py
```

Record a pytest run using the explicit runner:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m retracesoftware --recording recordings/tests.retrace -- -m pytest tests/ -q --tb=short
```

Record a module-based CLI:

```bash
RETRACE_RECORDING=recordings/cli.retrace python -m your_package.cli --input examples/input.json
```

Record a one-off command:

```bash
RETRACE_RECORDING=recordings/debug.retrace python -c "import random; print(random.random())"
```

Retrace creates the parent directory if needed and writes an executable `.retrace` file. The recording stores the command, working directory, Python version, Retrace checksums, replay metadata, and recorded boundary calls.

You can also record without the `.pth` hook:

```bash
python -m retracesoftware --recording recordings/run.retrace -- my_script.py
```

For more examples, see [Recording Python commands](docs/getting-started/recording-python-commands.md).

---

## Replay And Debug In VS Code

Open the same folder that contains your source and `.retrace` file:

```bash
code .
```

Then open the recording from the Retrace sidebar or right-click the `.retrace` file and choose `Open as Retrace Recording`.

The extension reads the replay binary path embedded in the `.retrace` shebang, indexes the recorded process tree, and launches replay debugging through the Go replay tool.

Set breakpoints in the recorded Python code and start replay. The debugger runs the recorded execution, not a live process.

See [VS Code extension](docs/getting-started/vscode-extension.md).

---

## Terminal Replay

Extract the recording:

```bash
./recordings/run.retrace --extract
```

That creates:

```text
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

This replays the recorded execution. It does not rerun the original command live.

---

## Other Editors And CLI

Retrace speaks the Debug Adapter Protocol, so DAP-compatible debuggers should be able to drive a Retrace replay session.

VS Code is the first supported editor. PyCharm, Zed, and other DAP clients are on the path.

A standalone CLI workflow is also coming, so you will not need an editor to drive a replay. Watch the Discussions for updates.

---

## Documentation

- [Full quickstart](quickstart/README.md)
- [Recording Python commands](docs/getting-started/recording-python-commands.md)
- [VS Code extension](docs/getting-started/vscode-extension.md)
- [Compatibility](COMPATIBILITY.md)

---

## Development From Source

Install from this checkout:

```bash
python -m pip install --upgrade pip wheel
python -m pip install "meson>=1.3" "meson-python>=0.18.0" "setuptools_scm>=8.0.4" ninja
python -m pip install --no-build-isolation -e .
```

The package includes Python code, native extensions built by Meson, module interception config, and the Go replay tooling used for extraction, terminal replay, and VS Code replay/debugging.

Supported wheels include the replay binary. Source/development installs can build it lazily if it is missing, which is why Go is required on `PATH`.

Run Python tests:

```bash
python -m pytest tests/ -v --tb=short
```

Run Go tests:

```bash
cd go
go test ./...
```

---

## Repository Layout

```text
quickstart/                         first-run demo and public quickstart flow
src/retracesoftware/__main__.py      CLI record/replay entrypoint
src/retracesoftware/autoenable.py    .pth startup hook implementation
src/retracesoftware/tape.py          recording file setup, checksums, and tape I/O
src/retracesoftware/install/         runtime patching and import hooks
src/retracesoftware/proxy/           record/replay boundary semantics
src/retracesoftware/modules/         stdlib and third-party interception config
src/retracesoftware/stream/          trace serialization
cpp/stream/                          native trace serialization support
src/retracesoftware/dap/             Python debugger protocol pieces
go/                                  replay extraction, indexing, and debug adapter tooling
vscode/                              VS Code extension
tests/ and dockertests/              unit, replay, and scenario tests
docs/                                user and maintainer documentation
```

---

## License

Apache-2.0
