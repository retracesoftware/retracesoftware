# Handle StrEnum Query Schemas in OpenAPI Generation

## Summary

Fixes OpenAPI schema generation for `enum.StrEnum` fields inside
`Query[msgspec.Struct]`.

When a query model contains a `StrEnum`, msgspec emits a schema where the query
model itself is resolved inline, but the enum field remains a `$ref` into
`#/components/schemas/...`. Because query parameter schema generation uses
`skip_registration=True`, the nested enum component was not registered, and
later parameter metadata resolution could raise:

```text
KeyError: 'TestEnum'
```

Fixes #1048.

## Root Cause

`SchemaGenerator(..., skip_registration=True)` returned the top-level query
schema from a temporary resolution context, but referenced nested components
were not registered in the global schema registry.

`ParameterGenerator` later splits that schema into individual OpenAPI
parameters and resolves each parameter property against the global registry. For
`enum.StrEnum`, this meant the parameter property referenced
`#/components/schemas/TestEnum`, but `TestEnum` was not present in the registry.

## Changes

- Add an explicit `register_referenced_components` option to `SchemaGenerator`.
- Enable that option from `ParameterGenerator`, where skipped schemas are later
  split into individual OpenAPI parameters.
- Register nested referenced components while still keeping the skipped
  top-level parameter model inline.
- Add a regression test covering `enum.StrEnum` inside a msgspec query model.

## Validation

Ran:

```bash
.venv312/bin/python -m pytest -o addopts='' tests/test_unit/test_plugins/test_msgspec/test_msgspec_schema.py::test_str_enum_query_schema -q
.venv312/bin/python -m pytest -o addopts='' tests/test_unit/test_plugins/test_msgspec/test_msgspec_schema.py -q
.venv312/bin/python -m pytest -o addopts='' tests -q -k "openapi or schema or parameter or query"
.venv312/bin/python -m pytest -o addopts='' tests -q --ignore=tests/test_integration/test_throttling/test_backends/test_redis_backend
```

Results:

```text
1 passed
31 passed
666 passed, 1814 deselected
2443 passed, 11 skipped
```

I also attempted the full suite without excluding Redis backend tests. The
non-Redis tests passed, but Redis-backed throttling tests failed during setup
because local Redis/socket access to `127.0.0.1:6379` was unavailable in this
environment.

## Debugging / Reproduction Note

We reproduced and inspected this failure using Retrace, our Python
record/replay debugger.

The replay showed the caller frame creating parameter metadata with a schema
reference to:

```text
#/components/schemas/TestEnum
```

At the failing registry lookup, `schema_name` was `"TestEnum"`,
`resolution_context` was `None`, and the global schema registry did not contain
`"TestEnum"`. That led to the `KeyError`.

The fix and regression test above do not depend on Retrace, but replay was
useful for confirming the runtime state across the parameter generation and
registry resolution frames.
