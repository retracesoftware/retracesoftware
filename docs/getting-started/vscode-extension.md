# VS Code Extension

The Retrace VS Code extension opens `.retrace` recordings and starts replay
debugging through the replay binary embedded in the recording.

## Install

Open VS Code, go to the Extensions sidebar, and search for:

```
Retrace Debug Extension
```

Install the extension published by:

```
RetraceSoftware
```

Restart VS Code if prompted.

## Create A Recording

From the quickstart folder:

```
RETRACE_RECORDING=recordings/flask.retrace python examples/flask_demo.py
```

## Open A Recording

Open the folder that contains the source file and the recording:

```
code .
```

Then use one of these paths:

- Open the Retrace sidebar and choose `Open Recording...`
- Right-click a `.retrace` file and choose `Open as Retrace Recording`

Select:

```
recordings/flask.retrace
```

## Debug

Open the source file:

```
examples/flask_demo.py
```

Set a breakpoint inside a route handler or inside `main()`, then start replay
from the Retrace view. During replay, you can inspect variables and navigate
through the recorded execution.

## Notes

- The extension works with `.retrace` recordings, not live processes.
- The recording contains a shebang that points at the replay binary used to
  extract and debug the recording.
- If replay fails in VS Code, first confirm terminal replay works:

```
./recordings/flask.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording recordings/flask.retrace --list_pids | head -1)
./recordings/flask.d/${ROOT_PID}.bin
```
