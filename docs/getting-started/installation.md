# Installation

Use a virtual environment for application dependencies. Retrace does not enable
itself just because it is installed; choose an explicit launcher mode when you
want to record.

## Requirements

- CPython 3.11 or 3.12
- macOS or Linux
- `pip`

Supported PyPI wheels include Retrace's replay binary, so normal
`pip install retracesoftware` users do not need Go installed. Go is only
required when building Retrace from source or on unsupported platforms where
`pip` has to build from source.

## Source Builds

If you are installing from a source checkout, make sure Go 1.25 or newer is on
`PATH` before building.

On macOS with Homebrew:

```
brew install go
```

On Linux, install Go 1.25 or newer from your distro packages or from
[go.dev/dl](https://go.dev/dl/).

## Create A Virtual Environment

For Python 3.12:

```
python3.12 -m venv .venv
source .venv/bin/activate
```

For Python 3.11:

```
python3.11 -m venv .venv
source .venv/bin/activate
```

## Install Retrace

```
python -m pip install --upgrade pip
python -m pip install retracesoftware
```

Check that Python can see the package:

```
python -m pip show retracesoftware
```

## One-Shot Recording

For a single experiment, run the command through `retracepython`:

```
retracepython --recording recordings/example.retrace your_script.py
```

Module commands work too:

```
retracepython --recording recordings/example.retrace -m your_package.cli
```

`retracepython` is one-shot: child processes that explicitly run ordinary
`python` are not automatically recorded.

## Create A Retrace-Aware Venv

For workflows where child `sys.executable` processes should also be recorded,
create a Retrace venv:

```
python -m retracesoftware venv .retrace-venv
.retrace-venv/bin/python your_script.py
```

The generated venv keeps `pip`, `ensurepip`, `venv`, and Retrace's own commands
untraced, but normal `python app.py`, `python -m app`, and `sys.executable`
children run through Retrace.

## Enable An Existing Python Environment

If you already have a configured Python environment, install an env-gated hook
into that environment:

```
python -m retracesoftware enable-hook
RETRACE=1 python your_script.py
```

The hook is inert unless `RETRACE=1`, `RETRACE_AUTO_DEBUG=1`,
`RETRACE_RECORDING`, or `RETRACE_CONFIG` is set. `pip`, `ensurepip`, `venv`,
multiprocessing helper bootstraps, and Retrace's own commands bypass recording.

To remove the hook:

```
python -m retracesoftware disable-hook
```

## Explicit Recording Without Launchers

You can also record through the underlying CLI:

```
python -m retracesoftware --recording recordings/example.retrace -- your_script.py
```

Everything after `--` is the target command.

For examples beyond a single `.py` file, see
[Recording Python Commands](recording-python-commands.md).

## Remove Legacy Auto-Enable Hooks

Older Retrace builds installed a `.pth` auto-enable hook. Remove it with:

```
python -m retracesoftware uninstall
```
