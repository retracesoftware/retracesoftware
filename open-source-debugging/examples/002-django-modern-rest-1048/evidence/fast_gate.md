# django-modern-rest #1048 Fast Gate

## Verdict

This is a valid internal VS Code replay/manual inspection example, not an
agent/MCP success case.

The issue is real and reproducible on the tested `django-modern-rest` commit,
and Retrace recorded the failing run. However, `retrace-agent inspect` stopped
on an earlier internal generated `TypeError` rather than the final useful DMR
`KeyError: 'TestEnum'`.

## Issue Facts

- Issue: https://github.com/wemake-services/django-modern-rest/issues/1048
- Title: `` `enum.StrEnum` fails in Query with KeyError during OpenAPI schema generation ``
- Status checked: 2026-06-03
- Status: open
- Labels observed during investigation: `bug`, `openapi`
- Reported final error: `KeyError: 'TestEnum'`

## Repo State

- Tested commit: `746a6070e3d0caae49d795794b95a13241a6cdef`
- Patch branch: `fix-strenum-query-openapi-schema`
- Patch commit: `6a107c4e4d1e9d4bbdea2347b1e23a856b65a19f`

## Reproduction

Reproducer:

```text
repro/reproduce_dmr_1048.py
```

Result before patch:

```text
KeyError: 'TestEnum'
```

Result after patch:

```text
Schema generation succeeds; the script reaches Django's normal command help.
```

## Retrace Inspect Limitation

Observed CLI inspect result:

- `cursor_available=true`
- `locals_available=true`
- `failure.reason=exception`
- `exception.type=TypeError`
- `exception.message="unhashable type: 'Not'"`
- `failure.location.filename="<string>"`
- `failure.location.function="__hash__"`

The current `stop_at_failure` command stopped at an earlier internal generated
exception. It did not reach the final DMR OpenAPI schema-generation frame:

```text
dmr/openapi/core/registry.py:112
```

## Manual VS Code Replay Evidence

Manual VS Code replay did reach the useful frames:

```text
dmr/openapi/generators/parameter.py:73
property_schema = Reference(ref="#/components/schemas/TestEnum")

dmr/openapi/core/registry.py:112
schema_name = "TestEnum"
resolution_context = None
self.schemas lacks "TestEnum"
```

## Recommendation

Use this example as a manual VS Code replay workflow demo only. Do not describe
it as an agent/MCP success case.
