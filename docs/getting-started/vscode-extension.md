# VS Code Extension

The Retrace VS Code extension opens `.retrace` recordings and lets you debug the
recorded execution.

Use this after you have created a recording.

## Install The Extension

Open VS Code, go to the Extensions sidebar, and search for:

```
Retrace Debug Extension
```

Install the extension published by:

```
RetraceSoftware
```

Restart VS Code if prompted.

## Create The Quickstart Recording

From the `quickstart` folder:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m retracesoftware --recording recordings/pytest.retrace -- -m pytest pytest_demo -q --tb=short
```

This creates:

```
recordings/pytest.retrace
```

The command exits nonzero because the demo contains one intentional failing
test. The recording is still the artifact you open for replay.

## Open The Folder

Open the folder that contains the source and the recording:

```
code .
```

If the `code` command is not installed, open VS Code manually and choose:

```
File -> Open Folder...
```

Then select the `quickstart` folder.

## Open The Recording

Use one of these paths:

- Open the Retrace sidebar and choose `Open Recording...`
- Right-click `recordings/pytest.retrace` and choose `Open as Retrace Recording`

Select:

```
recordings/pytest.retrace
```

The Retrace sidebar should show the recorded process tree. For the quickstart
demo there is normally one Python process.

## Set A Breakpoint

Open:

```
pytest_demo/checkout.py
```

Good first breakpoints:

- inside `build_receipt()`
- inside `calculate_tax()`
- inside `apply_promotion()`

## Start Replay

In the Retrace sidebar, start replay for the recorded Python process.

VS Code should enter a debug session and stop when replay reaches your
breakpoint. At that point you are looking at the recorded execution, not a live
rerun of the pytest demo.

During replay you can:

- inspect local variables
- continue to the next breakpoint
- step forward
- step backward
- restart the debug session and try another breakpoint

## What Success Looks Like

The debugger should stop in `pytest_demo/checkout.py` with normal VS Code debug
controls visible. The call stack and variables panels should update for the
recorded frame.

For this demo, values such as timestamps, UUIDs, and random numbers come from
the recording. They should not change just because you replay again.

`build_receipt()` is called by several tests. If replay stops in a passing test
first, continue until the call stack includes:

```
test_total_taxes_discounted_amount_once
```

## Terminal Sanity Check

If VS Code does not stop where expected, first confirm the recording replays in
the terminal:

```
./recordings/pytest.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording recordings/pytest.retrace --list_pids | head -1)
./recordings/pytest.d/${ROOT_PID}.bin
```

If terminal replay fails, debug the recording first. If terminal replay works
but VS Code does not stop, check that the breakpoint is in code that executed
during the recorded run.
