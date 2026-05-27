# Retrace Pytest Quickstart

This is a controlled preview of the pytest workflow for Retrace.

By the end, you will have:

- run a failing pytest test normally;
- recorded the same failed run as `recordings/pytest.retrace`;
- replayed the recorded failure in the terminal;
- opened the recording in VS Code;
- stopped inside the code that caused the failure and inspected runtime state;
- optionally created a CI-style replay bundle.

This quickstart takes about 10 minutes on a prepared machine.

The core loop is:

```text
failed pytest run
-> .retrace recording
-> replay the same failed execution
-> inspect runtime state
-> optional replay bundle for CI / AI workflows
```

This preview is deliberately small and controlled, but it is not a single-assertion toy. The demo includes filesystem reads through `tmp_path`, validation branches, calculated discounts, shipping, tax, UUIDs, time, random values, structured receipt data, and a realistic failure where one calculation happens in the wrong order.

The point is to validate the first user-visible loop:

> Record a failed pytest run once, replay it locally, and inspect the same execution instead of trying to reproduce the failure from logs and a traceback.

---

## Before You Start

Make sure you have:

- Python 3.12:
  ```bash
  python3.12 --version
  ```

- Go 1.25 or newer:
  ```bash
  go version
  ```

- Git

- VS Code for replay debugging

See `../COMPATIBILITY.md` for current platform details.

---

## What This Preview Shows

This preview shows the core product shape:

```text
failed pytest run
-> .retrace artifact
-> terminal replay
-> VS Code replay debugging
-> replay bundle for a human or AI agent
```

The local replay flow uses the `.retrace` recording directly.

The replay bundle flow shows the artifact shape we expect to use in CI:

```text
retrace-failed-run/
  trace.retrace
  retrace-manifest.json
  pytest.xml
  stdout.log
  replay.md
  pip-freeze.txt
```

If pytest passes, the recording is discarded. If pytest fails, the failed execution becomes something a developer can replay later.

---

## Recommended Preview Command

This quickstart keeps pytest plugin loading explicit so the run is small, repeatable, and easy to inspect.

The recommended command shape is:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m retracesoftware --recording recordings/pytest.retrace -- -m pytest pytest_demo -q --tb=short
```

This focuses the preview on Retrace's core loop:

> record a failed pytest execution once, replay it locally, and inspect the same runtime state.

Later versions should make this more natural with a first-class command such as:

```bash
retrace pytest -- pytest_demo -q --tb=short
```

---

## What Is In This Folder

```text
quickstart/
  pytest_demo/
    checkout.py
    tests/test_checkout.py
  recordings/
  make_pytest_bundle.py
  README.md
  requirements.txt
```

The main demo test is:

```text
pytest_demo/tests/test_checkout.py
```

It has a small checkout pipeline with inventory validation, promo rules loaded from JSON, loyalty discounts, shipping, tax, and audit fields.

Twelve tests pass and one test intentionally fails. The failure comes from `pytest_demo/checkout.py`, where tax is calculated before the loyalty discount is removed from the taxable base.

The code also uses values that normally change between runs, such as time, UUIDs, and random numbers. Replay demonstrates Retrace returning the recorded runtime values instead of touching the live world again.

---

# 1. Clone The Repo

```bash
git clone https://github.com/retracesoftware/retracesoftware.git
cd retracesoftware/quickstart
```

---

# 2. Check Go

Retrace installs with pip, but replay extraction and VS Code replay/debugging use Retrace's Go replay tool.

Check that Go is available:

```bash
go version
```

If that command fails, install Go before continuing.

On macOS with Homebrew:

```bash
brew install go
```

On Linux, install Go 1.25 or newer from your distro packages or from:

```text
https://go.dev/dl/
```

---

# 3. Create A Python 3.12 Virtual Environment

Check that Python 3.12 is available:

```bash
python3.12 --version
```

Create the virtual environment:

```bash
python3.12 -m venv .venv
```

Activate it:

```bash
source .venv/bin/activate
```

After activation, your terminal prompt should start with:

```text
(.venv)
```

---

# 4. Install Retrace And The Demo Dependencies

Install Retrace from PyPI:

```bash
python -m pip install --upgrade pip
python -m pip install retracesoftware
```

Check that the installation worked before continuing:

```bash
python -m pip show retracesoftware
```

Expected output includes:

```text
Name: retracesoftware
Version: ...
```

Install the quickstart dependencies:

```bash
python -m pip install -r requirements.txt
```

This installs pytest for the quickstart demo.

---

# 5. Run The Failing pytest Demo Normally

Run the demo without Retrace first:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest pytest_demo -q --tb=short
```

Expected result:

```text
FAILED pytest_demo/tests/test_checkout.py::test_total_taxes_discounted_amount_once
```

You should see one intentional failure.

This is a normal pytest run. Nothing has been recorded yet.

---

# 6. Record The Failed pytest Run With Retrace

Run the same pytest command through Retrace's explicit runner:

```bash
mkdir -p recordings

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m retracesoftware --recording recordings/pytest.retrace -- -m pytest pytest_demo -q --tb=short
```

The command exits non-zero because the test is supposed to fail. That is expected.

The important output is the recording:

```text
recordings/pytest.retrace
```

Check that the recording was written:

```bash
ls -lh recordings/pytest.retrace
```

Expected result:

```text
recordings/pytest.retrace
```

You now have a recording of the failed pytest execution.

---

# 7. Replay The Failed pytest Run In The Terminal

Terminal replay is the fastest way to confirm the recording is useful.

For this preview, the `.retrace` file is also an executable extractor. This interface will become a more natural `retrace ...` command in a later version.

Extract the replay files:

```bash
./recordings/pytest.retrace --extract
```

This creates:

```text
recordings/pytest.d/
```

Find the recorded process ID:

```bash
ROOT_PID=$(python -m retracesoftware --recording recordings/pytest.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"
```

Replay the recorded process:

```bash
./recordings/pytest.d/${ROOT_PID}.bin
```

Expected result:

```text
FAILED pytest_demo/tests/test_checkout.py::test_total_taxes_discounted_amount_once
```

Important:

> This is not rerunning pytest live. It is replaying the recorded failed execution.

Retrace is not starting a fresh test attempt. It is replaying the execution that was already recorded.

---

# 8. Open The Recording In VS Code

Open this folder:

```bash
code .
```

If `code` is not available, open VS Code manually and choose:

```text
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

```text
recordings/pytest.retrace
```

You can also right-click the `.retrace` file in the Explorer and choose:

```text
Open as Retrace Recording
```

---

# 9. Replay And Debug In VS Code

Open the source file:

```text
pytest_demo/checkout.py
```

Add a breakpoint inside:

```text
build_receipt
```

Then use the Retrace sidebar to start replaying the recorded process.

During replay, VS Code should stop on your breakpoint.

Inspect the checkout breakdown:

```text
subtotal_cents
item_discount_cents
loyalty_discount_cents
shipping_cents
taxable_cents
tax_cents
total_cents
```

Then step forward, step backward, and continue through the recorded failed execution.

`build_receipt` is called by several tests, so VS Code may stop at this breakpoint more than once. Continue until the call stack includes:

```text
test_total_taxes_discounted_amount_once
```

Then inspect the calculation that leads to the failing assertion.

You are done when:

- VS Code stops at your breakpoint;
- you can inspect runtime locals;
- replay reaches the same failing pytest assertion;
- you did not rerun the test live.

---

# 10. Create The CI-Style Replay Bundle

The local replay above uses `recordings/pytest.retrace` directly.

For CI, we expect to keep a replay bundle when pytest fails. The helper script creates that artifact shape.

Run:

```bash
python make_pytest_bundle.py
```

The script exits non-zero because the demo test intentionally fails. That is expected.

It writes:

```text
recordings/pytest-failed-run/
  trace.retrace
  retrace-manifest.json
  pytest.xml
  stdout.log
  replay.md
  pip-freeze.txt
```

`replay.md` contains copy-paste replay commands for a human developer or AI agent.

`retrace-manifest.json` captures replay metadata, but intentionally does not capture environment variables.

If pytest passes, the helper discards the recording because there is no failed execution to debug.

Expected result:

```text
recordings/pytest-failed-run/trace.retrace
recordings/pytest-failed-run/retrace-manifest.json
recordings/pytest-failed-run/replay.md
```

This is the artifact shape we expect to use in CI:

```text
failed pytest run
-> replay bundle artifact
-> local replay
-> later context pack / AI analysis
```

---

## Safety Note For CI Artifacts

Treat `.retrace` files and replay bundles like debug dumps.

They may contain runtime values from the test execution. If your tests touch secrets, credentials, production-like data, customer-like fixtures, API responses, database responses, or sensitive files, handle the artifact carefully.

For early CI use, we recommend:

- do not upload `.retrace` artifacts from untrusted fork PRs by default;
- use short artifact retention;
- avoid capturing all environment variables;
- treat replay bundles like logs or crash dumps;
- start with private repositories or trusted branches;
- review your own security policy before sending replay bundles to any AI system.

The preview manifest intentionally does not capture environment variables.

---

## Optional: AI-Assisted Debugging

This is an early manual workflow. The agent reads replay instructions and logs; future versions can expose structured runtime state directly.

Give an AI agent this prompt:

```text
A failed pytest run was recorded with Retrace.

Use recordings/pytest-failed-run/replay.md to replay the recorded failure.
Do not start by rerunning pytest live.

Read stdout.log, pytest.xml, and the source code.

Explain:
1. what failed,
2. why it failed,
3. the smallest code change that would fix it.
```

Today, this gives the AI a deterministic failed execution to rerun and inspect alongside the source and pytest output.

A future interface can expose structured locals, call stack, provenance, and reverse-debugging state directly to agents.

---

## Reset Recordings

To remove generated recordings and replay bundles:

```bash
rm -f recordings/*.retrace
rm -rf recordings/*.d
rm -rf recordings/pytest-failed-run
```

---

# Troubleshooting

## `python3.12: command not found`

Install Python 3.12 first, then create the virtual environment again:

```bash
python3.12 -m venv .venv
```

---

## `code: command not found`

Open VS Code manually and choose:

```text
File -> Open Folder...
```

Then select the `quickstart` folder.

---

## Permission denied when running the recording

If this command fails:

```bash
./recordings/pytest.retrace --extract
```

run:

```bash
chmod +x recordings/pytest.retrace
```

Then try the replay command again:

```bash
./recordings/pytest.retrace --extract
```

---

## Python version mismatch

Record and replay with the same virtual environment.

If you recorded with Python 3.12, replay with the same Python 3.12 environment.

---

## Recording did not create a `.retrace` file

Use the explicit Retrace runner for this pytest preview:

```bash
mkdir -p recordings

PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m retracesoftware --recording recordings/pytest.retrace -- -m pytest pytest_demo -q --tb=short
```

Then check:

```bash
ls -lh recordings/pytest.retrace
```

---

## A pytest plugin is missing

This quickstart disables pytest plugin autoloading so the preview is small and repeatable.

If your own suite needs a plugin, enable it directly after confirming the basic preview works.

For example:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p anyio tests
```

Broader plugin coverage is part of the pytest workflow roadmap.

---

## Terminal replay does not show the expected failure

Check that you are replaying the extracted process binary, not rerunning pytest live:

```bash
ROOT_PID=$(python -m retracesoftware --recording recordings/pytest.retrace --list_pids | head -1)
./recordings/pytest.d/${ROOT_PID}.bin
```

Expected result:

```text
FAILED pytest_demo/tests/test_checkout.py::test_total_taxes_discounted_amount_once
```

---

## VS Code stops at the breakpoint more than once

That is expected.

`build_receipt` is called by several tests. Continue until the call stack includes:

```text
test_total_taxes_discounted_amount_once
```

Then inspect the local values that lead to the failing assertion.

---

# Roadmap

The next pass is focused on making this workflow feel more natural in everyday pytest and CI use.

Planned improvements include:

- a first-class `retrace pytest -- ...` command;
- a built-in replay bundle command for CI artifacts;
- broader pytest plugin coverage, including coverage, parallel workers, timeout handling, and async combinations;
- richer AI-facing replay context, such as structured locals, stack, provenance, and failure-state summaries.

The goal is to keep the first workflow simple:

```text
Run pytest under Retrace.
If tests pass, discard the recording.
If tests fail, keep a replayable artifact.
Open it locally and debug the execution that actually happened.
```
