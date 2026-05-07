# Retrace Documentation

These docs describe the current `retracesoftware` package and `.retrace`
recording workflow.

## Start Here

1. [Installation](getting-started/installation.md)
2. [Quickstart](../quickstart/README.md)
3. [Recording Python Commands](getting-started/recording-python-commands.md)
4. [VS Code Extension](getting-started/vscode-extension.md)

That path gets a user from a clean Python environment to a recorded Flask demo,
real application command examples, and VS Code replay debugging.

## User Guides

- [Getting Started](getting-started/README.md)
- [Installation](getting-started/installation.md)
- [Quickstart](../quickstart/README.md)
- [Recording Python Commands](getting-started/recording-python-commands.md)
- [VS Code Extension](getting-started/vscode-extension.md)
- [Troubleshooting](troubleshooting.md)

## Reference

- [Reference Index](reference/README.md)
- [CLI Reference](reference/cli.md)
- [Environment Variables](reference/environment-variables.md)
- [Recording Files](reference/recording-files.md)

## Maintainer Docs

Start at [Internals](internals/README.md) if you are changing Retrace itself.

Current internal references:

- [Architecture](internals/architecture.md)
- [Module Layers](LAYERS.md)
- [Stream Architecture](STREAM.md)
- [Thread Replay](THREAD_REPLAY.md)
- [Debugger Design](DEBUGGER_DESIGN.md)
- [Debugging Retrace](DEBUGGING.md)
- [Cursors](cursors.md)

The maintainer docs are allowed to be lower level than the public guides. Public
guides should explain the stable workflow users run today.
