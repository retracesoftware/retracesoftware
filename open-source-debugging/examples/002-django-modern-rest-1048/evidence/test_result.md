# Test Results

Patch commit:

`6a107c4e4d1e9d4bbdea2347b1e23a856b65a19f`

Commands run:

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

Full suite note:

The full suite was also attempted without excluding Redis backend tests. The
non-Redis tests passed, but Redis-backed throttling tests failed during setup
because local Redis/socket access to `127.0.0.1:6379` was unavailable.
