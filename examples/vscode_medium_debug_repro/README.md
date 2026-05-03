# VS Code Medium Debug Repro

This is a small deterministic Python app for exercising Retrace's VS Code debugger path beyond hello world.

It covers:

- Multi-file Python execution.
- A class method call chain.
- A loop with repeated source-line hits.
- A handled exception path.
- Breakpoints in entry, service, and formatter files.
- Step, step back, continue, stack, and locals behaviour in VS Code.

## Record The Fixture

From this directory:

```bash
PYTHON_BIN=python3.12 ./record.sh
```

By default, `record.sh` installs `retracesoftware==0.2.3` into `.venv`.
To test a local checkout instead, run for example:

```bash
RETRACE_INSTALL_TARGET=../.. RETRACE_PIP_ARGS="--no-build-isolation" PYTHON_BIN=python3.12 ./record.sh
```

The normal program output should be:

```text
A100: Ada owes GBP 22.50 [ok]
B200: Grace owes GBP 45.00 [ok]
C300: Linus owes GBP 0.00 [empty]
```

The script writes:

- `medium.retrace`
- `medium.d/`
- `medium.code-workspace`

These generated files are intentionally ignored by git.

## Manual VS Code Checks

Open the generated workspace:

```bash
code medium.code-workspace
```

Then use `Retrace: Open Recording` and select `medium.retrace`.

Recommended single-breakpoint checks:

- `main.py:9` should find 1 hit.
- `formatter.py:2` should find 3 hits.
- `service.py:18` should find 5 hits.
- `service.py:14` should find 1 hit.

Recommended concurrent-breakpoint check:

- Clear all breakpoints.
- Set `main.py:9`.
- Set `service.py:18`.
- Start debugging / continue.

At the time this repro was added, the single-breakpoint checks passed, but the concurrent check could cancel one scan:

```text
breakpoint scan[1]: starting for main.py:9
breakpoint scan[2]: starting for service.py:18
breakpoint scan[1]: error: start replay: hello: context canceled
breakpoint scan[1]: complete, 0 hits found
breakpoint scan[2]: complete, 5 hits found
```

That suggests the likely issue is concurrent breakpoint scan replay startup/cancellation rather than source mapping.

## Direct DAP Probe

After recording the fixture, this probe sends two `setBreakpoints` requests back-to-back:

```bash
.venv/bin/python dap_concurrent_breakpoint_repro.py
```

This is not a replacement for the manual VS Code check. If this probe passes but VS Code shows `context canceled`, the remaining difference is likely in the VS Code/extension launch timing or breakpoint lifecycle rather than the basic DAP request shape.
