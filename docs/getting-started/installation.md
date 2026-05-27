# Installation

Use a virtual environment. Retrace installs a `.pth` auto-enable hook into the
active Python environment, so keeping it inside a venv makes setup and cleanup
obvious.

## Requirements

- CPython 3.11 or 3.12
- macOS or Linux
- `pip`
- Go 1.25 or newer on `PATH`

Retrace installs with `pip`, but replay extraction and VS Code replay/debugging
use Retrace's Go replay tool. Check Go before recording:

```
go version
```

If Go is missing, install it before continuing. On macOS with Homebrew:

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

## Enable Auto-Recording

Run this once per virtual environment:

```
python -m retracesoftware install
```

This copies `retracesoftware_autoenable.pth` into the active environment's
site-packages directory. Fresh Python processes in that environment will import
Retrace's auto-enable module at startup.

The hook records when you provide a recording path:

```
RETRACE_RECORDING=recordings/example.retrace python your_script.py
```

It can also record from a config preset or config file:

```
RETRACE_CONFIG=debug python your_script.py
```

For day-to-day use, prefer `RETRACE_RECORDING` because it makes the output path
obvious.

## Disable Auto-Recording

To remove the `.pth` hook from the active environment:

```
python -m retracesoftware uninstall
```

## Direct Recording Without The Hook

You can also record explicitly:

```
python -m retracesoftware --recording recordings/example.retrace -- your_script.py
```

Auto-recording remains the standard environment-variable workflow for ordinary
scripts and application commands.

For examples beyond a single `.py` file, see
[Recording Python Commands](recording-python-commands.md).
