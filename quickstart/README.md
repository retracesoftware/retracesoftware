# Retrace Pytest Quickstart

This is a controlled preview of the pytest workflow for Retrace. It takes
about 5 minutes.

By the end you will have a failing pytest run recorded as a `.retrace` file, a
small replay bundle, and a VS Code replay that can stop at a breakpoint inside
the code that caused the failure.

## Before You Start

Make sure you have:

1. Python 3.12 (`python3.12 --version`)
2. Git
3. VS Code for replay debugging

Supported PyPI wheels include Retrace's replay binary, so this quickstart does
not require Go.

See [../COMPATIBILITY.md](../COMPATIBILITY.md) for current platform details.

## Recommended Preview Command

This quickstart keeps pytest plugin loading explicit so the run is small,
repeatable, and easy to inspect:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
python -m retracesoftware --recording ... -- -m pytest ...
```

That command shape focuses the preview on Retrace's core loop: record a failed
pytest execution once, replay it locally, and inspect the same runtime state.

## What This Preview Shows

This preview shows the core product shape:

```
failed pytest run
-> .retrace artifact
-> terminal replay
-> VS Code replay debugging
-> replay bundle for a human or AI agent
```

The demo is deliberately small, but it is not a single assertion toy. It
includes filesystem reads through `tmp_path`, validation branches, calculated
discounts, shipping, tax, UUIDs, time, random values, structured receipt data,
and a realistic failure where one calculation happens in the wrong order.

## What We Want To Add Next

The next pass is focused on making this workflow feel even more natural in
everyday pytest and CI use:

- a first-class `retrace pytest -- ...` command
- a built-in replay bundle command for CI artifacts
- broader pytest plugin coverage, including coverage, parallel workers,
  timeout handling, and async combinations
- richer AI-facing replay context, such as structured locals, stack, and
  failure-state summaries

The point here is to validate the first user-visible loop: record a failed
pytest run once, replay it locally, and inspect the same execution instead of
rerunning the test live.

## What Is In This Folder

```
quickstart/
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

It has a small checkout pipeline with inventory validation, promo rules loaded
from JSON, loyalty discounts, shipping, tax, and audit fields. Twelve tests
pass and one test intentionally fails. The failure comes from
`pytest_demo/checkout.py`, where tax is calculated before the loyalty discount
is removed from the taxable base. The code also uses values that normally
change between runs, such as time, UUIDs, and random numbers, so replay
demonstrates Retrace returning the recorded runtime values instead of touching
the live world again.

## 1. Clone The Repo

```
git clone https://github.com/retracesoftware/retracesoftware.git
cd retracesoftware/quickstart
```

## 2. Create A Python 3.12 Virtual Environment

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

## 3. Install Retrace And The Demo Dependencies

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

This installs `pytest` for the quickstart demo.

## 4. Run The Failing pytest Demo Normally

Run the demo without Retrace first:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest pytest_demo -q --tb=short
```

You should see one intentional failure:

```
FAILED pytest_demo/tests/test_checkout.py::test_total_taxes_discounted_amount_once
```

This is a normal pytest run. Nothing has been recorded yet.

## 5. Record The Failed pytest Run With Retrace

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

## 6. Replay The Failed pytest Run In The Terminal

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

## 7. Open The Recording In VS Code

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

## 8. Replay And Debug In VS Code

Open the test file:

```
pytest_demo/tests/test_checkout.py
```

Find `test_total_taxes_discounted_amount_once` and set a breakpoint on:

```python
receipt = make_receipt()
```

Use the Retrace sidebar to start replaying the recorded process. VS Code should
stop at that breakpoint, and the top application frame in the Call Stack should
be `test_total_taxes_discounted_amount_once`.

While replay remains paused, open `pytest_demo/checkout.py` and add a second
breakpoint on:

```python
taxable_cents = discounted_subtotal_cents + shipping_cents
```

Press Continue. This selects the `build_receipt` invocation belonging to the
failing test, rather than one of the earlier successful tests. Inspect these
historical values in Locals:

```text
discounted_subtotal_cents = 7200
loyalty_discount_cents = 720
shipping_cents = 0
```

Step Over the taxable-base and tax calculations, then continue through the
multiline `total_cents` calculation. The recorded execution produces:

```text
taxable_cents = 7200
tax_cents = 594
total_cents = 7074
expected total_cents = 7015
```

The bug is that `loyalty_discount_cents` is subtracted from the final total but
is not subtracted from the taxable base. The correct taxable base is `6480`,
which produces `tax_cents = 535` and the expected total `7015`.

You are done when you can inspect these values and move forward or backward
through this recorded calculation without rerunning pytest live.

## 9. Optional: Create A Replay Bundle

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

This quickstart keeps pytest plugin loading explicit. If your own suite needs a
plugin, enable it directly after confirming the basic preview works. For
example:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p anyio tests
```

Broader plugin coverage is part of the pytest workflow roadmap.
