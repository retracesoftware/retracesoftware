# Getting Started

These guides cover the public workflow for a new Retrace user.

The included Flask quickstart takes about 5 minutes. Before starting it, check
that Python 3.12, Go 1.25 or newer, Git, and VS Code are installed. The guide
also shows how to confirm the Retrace package is installed and that the
`.retrace` recording was created before opening VS Code.

Read them in order:

1. [Installation](installation.md)
2. [Quickstart](../../quickstart/README.md)
3. [Recording Python Commands](recording-python-commands.md)
4. [VS Code Extension](vscode-extension.md)

The current recommended flow is:

```
go version
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install retracesoftware
python -m pip show retracesoftware
python -m retracesoftware install
RETRACE_RECORDING=recordings/run.retrace python your_script.py
code .
```

Then install the Retrace VS Code extension from the Marketplace and open the
`.retrace` recording from VS Code. Add breakpoints in your source files and
start the Retrace debug configuration to replay the recorded execution.

Terminal replay is also available when you want a quick sanity check:

```
./recordings/run.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording recordings/run.retrace --list_pids | head -1)
./recordings/run.d/${ROOT_PID}.bin
```
