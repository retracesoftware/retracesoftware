# Recording Python Commands

Retrace records Python executions. For one command, use `retracepython`. For an
environment where child `python` processes should also be recorded, use
`python -m retracesoftware venv .retrace-venv` and run that venv's Python.

## Script Files

```
retracepython --recording recordings/app.retrace app.py
```

With arguments:

```
retracepython --recording recordings/app.retrace app.py --port 8000 --debug
```

## Python Modules

Use this for package entrypoints such as application CLIs. Replace
`your_package.cli` and the arguments with the command your project already uses:

```
retracepython --recording recordings/cli.retrace -m your_package.cli --input examples/input.json
```

For a Flask app that you normally run through Flask's module CLI:

```
retracepython --recording recordings/server.retrace -m flask --app app run
```

## Pytest

For pytest, use the explicit runner from the quickstart:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 retracepython \
  --recording recordings/pytest.retrace \
  -m pytest tests/ -q --tb=short
```

## Other Python Tools

If the tool is normally run through Python, record it the same way:

```
retracepython --recording recordings/tool.retrace -m your_tool --input examples/input.json
```

## Inline Python

```
retracepython --recording recordings/inline.retrace -c "import random; print(random.random())"
```

## Auto-Debug On Failure

Set `RETRACE_AUTO_DEBUG=1` to run the AI debugger automatically if the recorded
command exits nonzero:

```
RETRACE_AUTO_DEBUG=1 retracepython app.py
```

On failure, Retrace runs `retrace-ai-driver` with `--tool-executor dap` against
the recording. The driver starts the Retrace DAP server and drives it through
the `retrace-ai-service`/provider configuration supplied to the driver.
Configure the driver with `RETRACE_AI_SERVER`, `RETRACE_API_KEY`, and
`RETRACE_REPLAY_BIN`. `RETRACE_AI_DRIVER_COMMAND` can override the packaged
driver command for development.
`RETRACE_AI_SERVER` defaults to
`https://retrace-ai-service.retracesoftware.workers.dev`.

Successful runs delete the default trace file. If you pass `--recording` or set
`RETRACE_RECORDING`, Retrace keeps that explicit trace even when the command
succeeds.

## Existing-Environment Hook

If you enable the active Python environment:

```
python -m retracesoftware enable-hook
```

then these forms also work while the hook is installed:

```
RETRACE_RECORDING=recordings/app.retrace python app.py
RETRACE_CONFIG=debug python -m your_package.cli
```

Disable that hook with `python -m retracesoftware disable-hook`.

## After Recording

Open the `.retrace` file in VS Code with the Retrace Debug Extension, or extract
and replay it in the terminal:

```
./recordings/app.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording recordings/app.retrace --list_pids | head -1)
./recordings/app.d/${ROOT_PID}.bin
```
