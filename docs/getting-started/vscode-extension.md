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
RETRACE_RECORDING=recordings/flask.retrace python examples/flask_demo.py
```

This creates:

```
recordings/flask.retrace
```

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
- Right-click `recordings/flask.retrace` and choose `Open as Retrace Recording`

Select:

```
recordings/flask.retrace
```

The Retrace sidebar should show the recorded process tree. For the quickstart
demo there is normally one Python process.

## Set A Breakpoint

Open:

```
examples/flask_demo.py
```

Good first breakpoints:

- inside `health()`
- inside `create_user()`
- inside `main()`

## Start Replay

In the Retrace sidebar, start replay for the recorded Python process.

VS Code should enter a debug session and stop when replay reaches your
breakpoint. At that point you are looking at the recorded execution, not a live
rerun of the Flask demo.

During replay you can:

- inspect local variables
- continue to the next breakpoint
- step forward
- step backward
- restart the debug session and try another breakpoint

## What Success Looks Like

The debugger should stop in `examples/flask_demo.py` with normal VS Code debug
controls visible. The call stack and variables panels should update for the
recorded frame.

For this demo, values such as timestamps, UUIDs, and random numbers come from
the recording. They should not change just because you replay again.

## Terminal Sanity Check

If VS Code does not stop where expected, first confirm the recording replays in
the terminal:

```
./recordings/flask.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording recordings/flask.retrace --list_pids | head -1)
./recordings/flask.d/${ROOT_PID}.bin
```

If terminal replay fails, debug the recording first. If terminal replay works
but VS Code does not stop, check that the breakpoint is in code that executed
during the recorded run.
