# VS Code Medium-Plus Debug Repro

This fixture is a deterministic Python 3.12 package-layout app for exercising a richer VS Code debugger path.

It covers:

- Package imports under `app/`.
- Dataclasses and object attributes.
- Nested list/dict input data.
- A generator expression inside `sum(...)`.
- A handled exception path.
- A deterministic child thread using `queue.Queue` and `thread.join()`.

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
A100: Ada items=3 total=GBP 22.50 status=ok audit=0:A100:ada
B200: Grace items=2 total=GBP 45.00 status=ok audit=1:B200:grace
C300: Linus items=0 total=GBP 0.00 status=empty audit=2:C300:linus
```

The script writes:

- `medium-plus.retrace`
- `medium-plus.d/`
- `medium-plus.code-workspace`

These generated files are intentionally ignored by git.

## Manual VS Code Checks

Open the generated workspace:

```bash
code medium-plus.code-workspace
```

Then use `Retrace: Open Recording` and select `medium-plus.retrace`.

Recommended single-breakpoint checks:

- `app/main.py:9` should find 1 hit.
- `app/service.py:15` should find 1 hit.
- `app/service.py:17` is the generator-expression total calculation.
- `app/audit.py:15` should hit in the deterministic child thread.
- `app/formatter.py:2` should hit when formatting summaries.

## Observed Generator-Step Failure

At the time this repro was added, `app/main.py:9` was a clean pass.

The generator-expression line `app/service.py:17` also scanned and continued successfully, but repeated stepping through the line produced an internal cursor assertion:

```text
navigation failed (step): run_to_cursor: internal_error: AssertionError:
Traceback (most recent call last):
  File ".../retracesoftware/control_runtime.py", line 477, in control_event_loop
    assert isinstance(cursor_dict, dict)
AssertionError
, staying at current position
```

The line under test is:

```python
subtotal = sum(price for price in order.items if price > 0)
```

This suggests `run_to_cursor` can return a non-dict cursor result while stepping through generator-expression/comprehension-like code.

This repro is currently manual because the failure was observed through the VS Code stepping path.
