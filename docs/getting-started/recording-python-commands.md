# Recording Python Commands

Retrace records Python executions. Use the same Python command you would
normally run, and put `RETRACE_RECORDING=...` before it.

First enable auto-recording in the active virtual environment:

```
python -m retracesoftware install
```

Then run your program.

## Script Files

```
RETRACE_RECORDING=recordings/app.retrace python app.py
```

With arguments:

```
RETRACE_RECORDING=recordings/app.retrace python app.py --port 8000 --debug
```

## Python Modules

Use this for package entrypoints such as application CLIs. Replace
`your_package.cli` and the arguments with the command your project already uses:

```
RETRACE_RECORDING=recordings/cli.retrace python -m your_package.cli --input examples/input.json
```

For a Flask app that you normally run through Flask's module CLI:

```
RETRACE_RECORDING=recordings/server.retrace python -m flask --app app run
```

## Pytest Or Other Python Tools

If the tool is normally run through Python, record it the same way:

```
RETRACE_RECORDING=recordings/pytest.retrace python -m pytest tests/
```

## Inline Python

```
RETRACE_RECORDING=recordings/inline.retrace python -c "import random; print(random.random())"
```

## Without The Auto-Enable Hook

You can record explicitly without `python -m retracesoftware install`:

```
python -m retracesoftware --recording recordings/app.retrace -- app.py --port 8000
```

For modules:

```
python -m retracesoftware --recording recordings/cli.retrace -- -m your_package.cli --input examples/input.json
```

Everything after `--` is the Python command Retrace will run.

## After Recording

Open the `.retrace` file in VS Code with the Retrace Debug Extension, or extract
and replay it in the terminal:

```
./recordings/app.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording recordings/app.retrace --list_pids | head -1)
./recordings/app.d/${ROOT_PID}.bin
```
