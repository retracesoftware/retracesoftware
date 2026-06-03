# Test result

Date: 2026-06-03

## Environment

- Validation venv: `.venv-validation` inside the local nbconvert candidate clone.
- Python: `Python 3.11.9`
- nbconvert clone: `v7.17.1`, commit `78ed30837a607deab7cf0a12dca072bf3f63417a`
- Installed package: editable `nbconvert==7.17.1` from the patched candidate clone.
- Installed test extra: `nbconvert[test]`
- Key optional test dependencies present:
  - `ipython==9.14.0`
  - `ipykernel==7.2.0`
  - `ipywidgets==8.1.8`
  - `flaky==3.8.1`
  - `pytest==9.0.3`

The full-suite retry used local writable IPython/Jupyter directories:

```text
IPYTHONDIR=.ipython-validation
JUPYTER_CONFIG_DIR=.jupyter-config-validation
JUPYTER_DATA_DIR=.jupyter-data-validation
```

## Focused tests

Command:

```bash
.venv-validation/bin/python -m pytest \
  tests/filters/test_widgetsdatatypefilter.py \
  tests/filters/test_datatypefilter.py \
  -q
```

Result:

```text
5 passed in 3.29s
```

Status:

```text
passed
```

## Broader filter subset

Command:

```bash
.venv-validation/bin/python -m pytest tests/filters -q
```

Result:

```text
54 passed, 5 skipped in 5.51s
```

Skipped tests were pandoc-dependent.

Status:

```text
passed
```

## Full suite

Command:

```bash
IPYTHONDIR=.ipython-validation \
JUPYTER_CONFIG_DIR=.jupyter-config-validation \
JUPYTER_DATA_DIR=.jupyter-data-validation \
.venv-validation/bin/python -m pytest -q
```

Result:

```text
290 passed, 41 skipped in 120.08s (0:02:00)
```

Skipped tests were for unavailable optional external tools or extras including
`pandoc`, `xelatex`, `PyQtWebEngine`, `Playwright`, and `inkscape`.

Status:

```text
passed
```

## Notes On Earlier Local Attempt

Before creating the complete validation venv, the broader filter run failed in
the Retrace repo venv because `IPython` was missing. The full suite also failed
collection because the `flaky` pytest marker/plugin was missing.

After installing `nbconvert[test]`, those blockers disappeared.

A sandboxed full-suite attempt in the complete venv still failed because the
sandbox blocked multiprocessing semaphore checks and Jupyter kernel loopback
socket binding. The final full-suite result above was produced outside the
sandbox with local writable IPython/Jupyter directories.

## Reduced Repro Before Patch

Command:

```bash
python repro/reproduce_nbconvert_1731.py
```

Run against the unpatched installed `nbconvert==7.17.1`.

Result:

```text
KeyError: 'state'
```

Relevant traceback location:

```text
nbconvert/filters/widgetsdatatypefilter.py:58
metadata["widgets"][WIDGET_STATE_MIMETYPE]["state"]
```

Status:

```text
reproduces failure
```

## Reduced Repro After Patch

Command:

```bash
.venv-validation/bin/python repro/reproduce_nbconvert_1731.py
```

Run against the patched editable candidate clone.

Result:

```text
passed with no output
```

Status:

```text
passes
```

## PR Validation Status

The candidate patch passed:

- focused regression tests;
- the full `tests/filters` subset;
- the full nbconvert test suite available in this local environment.

The maintainer PR draft is ready for review. The upstream PR has not been
submitted.
