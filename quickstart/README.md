# Retrace Pytest Quickstart

This is a controlled preview of the pytest workflow for Retrace. It takes
about 5 minutes.

By the end you will have a failing pytest run recorded as a `.retrace` file, a
small replay bundle, and a VS Code replay that can stop at a breakpoint inside
the code that caused the failure.

## Before You Start

Make sure you have:

1. Python 3.12 (`python3.12 --version`)
2. Go 1.25 or newer (`go version`)
3. Git
4. VS Code for replay debugging

See [../COMPATIBILITY.md](../COMPATIBILITY.md) for current platform details.

## Current Preview Scope

This quickstart intentionally uses a narrow pytest setup:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
python -m retracesoftware --recording ... -- -m pytest ...
```

`PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` prevents pytest from automatically loading
every third-party pytest plugin installed in the environment. That keeps the
preview focused on the core Retrace workflow: recording and replaying the
failed test execution.

The weak side is that this is not the same as a full plugin-heavy production
pytest suite. Some teams rely on auto-loaded plugins such as `pytest-cov`,
`pytest-xdist`, `pytest-timeout`, `pytest-asyncio`, or internal plugins. Those
paths are still being hardened. For this preview, use the command shape shown
below.

The quickstart also uses the explicit Retrace runner instead of the `.pth`
auto-enable hook. This avoids child Python subprocesses inheriting
`RETRACE_RECORDING` and accidentally changing the recording shape.

## What Is In This Folder

```
quickstart/
  examples/
    flask_demo.py
    simple_demo.py
  pytest_demo/
    checkout.py
    tests/test_checkout.py
  recordings/
  make_pytest_bundle.py
  README.md
  requirements.txt
```

The main demo is:

```
pytest_demo/tests/test_checkout.py
```

It has two passing tests and one intentional failing test. The failure comes
from `pytest_demo/checkout.py`, where the checkout total accidentally adds tax
twice. The function also uses values that normally change between runs, such as
time, UUIDs, and random numbers, so replay demonstrates Retrace returning the
recorded runtime values instead of touching the live world again.

## 1. Clone The Repo

```
git clone https://github.com/retracesoftware/retracesoftware.git
cd retracesoftware/quickstart
```

## 2. Check Go

Retrace installs with `pip`, but replay extraction and VS Code
replay/debugging use Retrace's Go replay tool. Check that Go is available:

```
go version
```

If that command fails, install Go before continuing.

On macOS with Homebrew:

```
brew install go
```

On Linux, install Go 1.25 or newer from your distro packages or from
[go.dev/dl](https://go.dev/dl/).

## 3. Create A Python 3.12 Virtual Environment

Check that Python 3.12 is available:

```
python3.12 --version
```

Create the virtual environment:

```
python3.12 -m venv .venv
```

Activate it:

```
source .venv/bin/activate
```

After activation, your terminal prompt should start with:

```
(.venv)
```

## 4. Install Retrace And The Demo Dependencies

Install Retrace from PyPI:

```
python -m pip install --upgrade pip
python -m pip install retracesoftware
```

Check that the installation worked before continuing:

```
python -m pip show retracesoftware
```

You should see package details that include:

```
Name: retracesoftware
Version: ...
```

Install the quickstart dependencies:

```
python -m pip install -r requirements.txt
```

This installs `pytest` for the main quickstart and `flask` for the optional app
demo at the bottom of this file.

## 5. Run The Failing pytest Demo Normally

Run the demo without Retrace first:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest pytest_demo -q --tb=short
```

You should see one intentional failure:

```
FAILED pytest_demo/tests/test_checkout.py::test_total_applies_tax_and_discount_once
```

This is a normal pytest run. Nothing has been recorded yet.

## 6. Record The Failed pytest Run With Retrace

Run the same pytest command through Retrace's explicit runner:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m retracesoftware --recording recordings/pytest.retrace -- -m pytest pytest_demo -q --tb=short
```

The command exits nonzero because the test is supposed to fail. That is okay.
The important output is the recording:

```
recordings/pytest.retrace
```

Check that the recording was written:

```
ls -lh recordings/pytest.retrace
```

## 7. Replay The Failed pytest Run In The Terminal

Terminal replay is the fastest way to confirm the recording is useful.

Extract the replay files:

```
./recordings/pytest.retrace --extract
```

This creates:

```
recordings/pytest.d/
```

Find the recorded process id:

```
ROOT_PID=$(python -m retracesoftware --recording recordings/pytest.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"
```

Replay the recorded process:

```
./recordings/pytest.d/${ROOT_PID}.bin
```

You should see the same pytest failure replay locally. Retrace is not running a
fresh live pytest attempt here; it is replaying the recorded failed execution.

## 8. Open The Recording In VS Code

Open this folder:

```
code .
```

If `code` is not available, open VS Code manually and choose:

```
File -> Open Folder...
```

Then select the `quickstart` folder.

Install the Retrace extension:

1. Open the Extensions sidebar.
2. Search for `Retrace Debug Extension`.
3. Install the extension published by `RetraceSoftware`.

Open the recording:

1. Open the Retrace sidebar.
2. Click `Open Recording...`.
3. Select:

```
recordings/pytest.retrace
```

You can also right-click the `.retrace` file in the Explorer and choose:

```
Open as Retrace Recording
```

## 9. Replay And Debug In VS Code

Open the source file:

```
pytest_demo/checkout.py
```

Add a breakpoint inside:

```
build_receipt
```

Then use the Retrace sidebar to start replaying the recorded process.

During replay, VS Code should stop on your breakpoint. You can inspect local
variables such as `subtotal_cents`, `tax_cents`, `discount_cents`, and
`total_cents`, then step forward, step backward, and continue through the
recorded failed execution.

You are done when VS Code stops at your breakpoint and the replay reaches the
same failing pytest assertion without rerunning the test live.

## 10. Optional: Create A Replay Bundle

The helper script creates the artifact shape used by the pytest/CI preview:

```
python make_pytest_bundle.py
```

The script exits nonzero because the demo test fails. That is expected. It
writes:

```
recordings/pytest-failed-run/
  trace.retrace
  retrace-manifest.json
  pytest.xml
  stdout.log
  replay.md
  pip-freeze.txt
```

`replay.md` contains copy-paste replay commands for a human or an AI agent. The
manifest intentionally does not capture environment variables.

If pytest passes, the helper discards the recording because there is no failed
execution to debug.

## Optional: AI-Assisted Debugging

Give an AI agent this prompt:

```
A failed pytest run was recorded with Retrace.

Use recordings/pytest-failed-run/replay.md to replay the recorded failure.
Do not start by rerunning pytest live.
Read stdout.log, pytest.xml, and the source code.
Explain:
1. what failed,
2. why it failed,
3. the smallest code change that would fix it.
```

Today, this gives the AI a deterministic failed execution to rerun and inspect
alongside the source and pytest output. A future interface can expose structured
locals, call stack, and reverse-debugging state directly to agents.

## Optional: Replay The Flask App Demo

The Flask demo shows the same recording and replay model on a small Python
application.

Run normally:

```
python examples/flask_demo.py
```

Record:

```
RETRACE_RECORDING=recordings/flask.retrace python examples/flask_demo.py
```

Extract:

```
./recordings/flask.retrace --extract
```

Find the process id:

```
ROOT_PID=$(python -m retracesoftware --recording recordings/flask.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"
```

Replay:

```
./recordings/flask.d/${ROOT_PID}.bin
```

## Optional: Try The Smaller Demo

Run normally:

```
python examples/simple_demo.py
```

Record:

```
RETRACE_RECORDING=recordings/simple.retrace python examples/simple_demo.py
```

Extract:

```
./recordings/simple.retrace --extract
```

Find the process id:

```
ROOT_PID=$(python -m retracesoftware --recording recordings/simple.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"
```

Replay:

```
./recordings/simple.d/${ROOT_PID}.bin
```

## Reset Recordings

```
rm -f recordings/*.retrace
rm -rf recordings/*.d
rm -rf recordings/pytest-failed-run
```

## Troubleshooting

### `python3.12: command not found`

Install Python 3.12 first, then create the virtual environment again:

```
python3.12 -m venv .venv
```

### `code: command not found`

Open VS Code manually and choose:

```
File -> Open Folder...
```

### Permission denied when running the recording

Run:

```
chmod +x recordings/pytest.retrace
```

Then try the replay command again.

### Python version mismatch

Record and replay with the same virtual environment. If you recorded with
Python 3.12, replay with the same Python 3.12 environment.

### Recording did not create a `.retrace` file

Use the explicit Retrace runner for this pytest preview:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m retracesoftware --recording recordings/pytest.retrace -- -m pytest pytest_demo -q --tb=short
```

### A pytest plugin is missing

This quickstart disables pytest plugin autoload on purpose. If your own suite
needs a plugin, explicitly enable it after confirming the basic preview works.
For example:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p anyio tests
```

Plugin-heavy suites are part of the next hardening pass for Retrace's pytest
workflow.
