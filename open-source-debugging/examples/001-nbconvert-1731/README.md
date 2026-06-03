# 001: nbconvert KeyError `state`

Private pipeline case for `jupyter/nbconvert#1731`.

This case shows a real third-party Python failure reproduced, recorded,
replayed, and inspected with Retrace. It is structured for eventual placement
under `examples/open-source-debugging/001-nbconvert-1731/`, but it is not ready
for public release yet.

## Issue

- Primary issue: https://github.com/jupyter/nbconvert/issues/1731
- Related issue: https://github.com/jupyter/nbconvert/issues/2127
- Failure: `KeyError: 'state'`

## Runtime fact

The runtime `metadata` local contains the widget-state mimetype object:

```python
{
    "widgets": {
        "application/vnd.jupyter.widget-state+json": {
            "version_major": 2,
            "version_minor": 0,
        }
    }
}
```

The nested `state` key is absent. The recorded nbconvert code indexes:

```python
metadata["widgets"][WIDGET_STATE_MIMETYPE]["state"]
```

and raises `KeyError: 'state'`.

## Contents

- `case.yaml`: case metadata and validation status
- `CLAIMS.md`: safe and unsafe claims
- `repro/`: portable reduced reproducer and pinned environment
- `fix/patch.diff`: candidate maintainer patch
- `evidence/`: CLI, VS Code, and test evidence summaries
- `maintainer_pr_draft.md`: maintainer-facing PR draft

## Positioning

This is a workflow demo only. It is not a benchmark, does not claim Retrace is
necessary to solve this bug, and does not claim uplift over static analysis.
