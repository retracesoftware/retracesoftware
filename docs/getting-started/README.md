# Getting Started

These guides cover the public workflow for a new Retrace user.

Read them in order:

1. [Installation](installation.md)
2. [Quickstart](../../quickstart/README.md)
3. [VS Code Extension](vscode-extension.md)

The current recommended flow is:

```
python -m pip install retracesoftware
python -m retracesoftware install
RETRACE_RECORDING=recordings/run.retrace python your_script.py
./recordings/run.retrace --extract
```

Then replay the extracted PidFile in the terminal or open the recording in the
Retrace VS Code extension.
