# Henry Transitive Tests Todo

This directory contains **Transitive Compatibility Candidate** tests.

These are pure-Python libraries we expect to work because they sit above already-supported Python runtime behaviour or already-tested libraries such as Click, Rich, Requests, HTTPX, Flask, FastAPI, Starlette, Pydantic, JSONSchema, Packaging, and Black.

They are not formal support claims until the tests pass in a configured environment.

Run:

```bash
python -m pip install -r "dockertests/henry transitive tests todo/requirements.txt"
python -m pytest "dockertests/henry transitive tests todo" -v --tb=short
```

Each test writes a tiny deterministic script, records it with Retrace, replays it, and checks replay stdout matches recorded stdout. Missing libraries are skipped with `pytest.importorskip`.
