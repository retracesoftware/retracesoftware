# Subprocess Env Capture Replay Repro

This fixture is a minimal repro for a subprocess replay divergence seen on
`cadd2ce`.

It covers:

- `subprocess.run(...)`.
- A child Python script file.
- `capture_output=True`.
- `text=True`.
- A parent-supplied modified `env`.
- Captured child stdout and stderr.

The reduced trigger is the custom `env`. The same child-script capture shape
without a modified `env` replayed successfully during reduction.

## Run The Repro

From this directory:

```bash
PYTHON_BIN=python3.12 ./record.sh
```

By default, `record.sh` installs `retracesoftware` into `.venv`.
To test a local checkout instead, run for example:

```bash
RETRACE_INSTALL_TARGET=../.. RETRACE_PIP_ARGS="--no-build-isolation" PYTHON_BIN=python3.12 ./record.sh
```

Expected plain run and record output:

```text
CAPTURED CHILD alpha 7 ERR ok 0
```

The script writes:

- `subprocess-env-capture.retrace`
- `subprocess-env-capture.d/`

At the time this repro was added, the recording index reported `children 0`
and PidFile replay failed at `_posixsubprocess.fork_exec`:

```text
Checkpoint difference: {'function': wrapped_function:_posixsubprocess.fork_exec -> builtin_function_or_method:_posixsubprocess.fork_exec, 'args': ([<str>, <str>, <str>], (<bytes>), True, (<int>), ...), 'kwargs': {}} was expecting {'function': wrapped_function:_posixsubprocess.fork_exec -> builtin_function_or_method:_posixsubprocess.fork_exec, 'args': ([<str>, <str>, <str>], (<bytes>), True, (<int>), ...), 'kwargs': {}}
replay: replay exited: exit status 1
```
