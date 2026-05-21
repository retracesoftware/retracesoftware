```md
# Contributing to Retrace

Thank you for your interest in contributing to Retrace.

Retrace is a reverse debugger and deterministic record-replay system for Python. Contributions are welcome, especially around bug reports, reproducible examples, documentation, tests, compatibility reports, and small focused fixes.

## Good first contributions

Useful contributions include:

- Reporting Python packages or frameworks that do or do not work with Retrace.
- Creating minimal reproducible examples for replay divergence.
- Improving installation, setup, and troubleshooting documentation.
- Adding tests for Python language features, standard library behaviour, Flask, Django, threading, forking, or pytest workflows.
- Improving error messages.
- Fixing small bugs.

## Reporting bugs

When filing a bug, please include as much of the following as possible:

- Operating system and version.
- Python version.
- Retrace version or commit SHA.
- How you installed Retrace.
- The command you ran.
- The expected behaviour.
- The actual behaviour.
- A minimal reproduction, if possible.
- Any relevant traceback or logs.

Please do not upload production trace files publicly. Trace files may contain sensitive application data, secrets, customer data, or internal system details.

## Security issues

Please do not report security vulnerabilities in public GitHub issues.

See [SECURITY.md](SECURITY.md) for how to report security issues privately.

## Development setup

Clone the repository:

```bash
git clone https://github.com/retracesoftware/retracesoftware.git
cd retracesoftware
````

Install the project using the current instructions in the README.

Run the relevant tests before submitting a pull request. If you are unsure which tests apply, explain what you ran in the pull request description.

## Pull requests

Before opening a pull request:

* Keep the change focused.
* Include tests where practical.
* Update documentation if behaviour changes.
* Explain the problem and the approach.
* Mention any known limitations or follow-up work.

Large architectural changes should usually start as an issue or discussion before a pull request.

## Design principles

Retrace prioritises:

* Deterministic replay.
* Low recording overhead.
* Minimal observer effect.
* Clear boundaries between application code and external/non-deterministic operations.
* Practical debugging workflows for Python developers and AI coding agents.

When in doubt, favour small, reviewable changes that make record/replay more reliable or easier to understand.
