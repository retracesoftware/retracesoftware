# Claims

## Safe claim

Retrace can reproduce, replay and inspect the `nbconvert #1731`
`KeyError: 'state'` failure. In CLI and VS Code workflows, Retrace exposes the
runtime `metadata` local showing that the widget-state mimetype object is
present but the nested `state` key is absent.

## Unsafe claims

Do not claim:

- Retrace beats static analysis on this case.
- This proves benchmark uplift.
- This bug is impossible to solve from source alone.
- Retrace has full value-level provenance here.
- This one case is a productized benchmark.
- This is the flagship case for Retrace's runtime-needed advantage.
