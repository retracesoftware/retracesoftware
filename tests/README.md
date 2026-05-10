# Pytest Harnesses

The `tests/` tree contains unit, integration, and replay-focused pytest tests.
Most files target one subsystem directly. The dockertest in-process harness is
special because it mirrors selected `dockertests/tests/*/test.py` scenarios
through `tests.runner.Runner`.

## In-Process Dockertest Harness

File:

```text
tests/test_dockertests_inprocess.py
```

Run it:

```bash
cd retracesoftware
python -m pytest -q tests/test_dockertests_inprocess.py
```

Choose the Retrace config:

```bash
python -m pytest -q tests/test_dockertests_inprocess.py --retrace-config normal
python -m pytest -q tests/test_dockertests_inprocess.py --retrace-config debug
```

Run one scenario:

```bash
python -m pytest -q 'tests/test_dockertests_inprocess.py::test_dockertest_inprocess[simple_test]'
```

`normal` uses Runner with `debug=False`, matching Retrace's ordinary release
record/replay lane. `debug` uses Runner with `debug=True`, which exercises the
richer checkpoint/protocol path used while diagnosing desyncs.

## What It Tests

Each scenario is a finite Python function adapted from a dockertest body. The
harness records the function into an in-memory `IOMemoryTape`, replays it from
that same tape, and asserts that replay consumed exactly what record produced.

The pytest case launches a child Python process per scenario. That child-process
isolation is only for pytest hygiene: a failed Retrace patch or replay attempt
cannot corrupt process-global module state for the next parameter. Inside the
child, record/replay is still pure `tests.runner.Runner` behavior.

The harness does not use:

- Docker
- `python -m retracesoftware --recording`
- `.pth` auto-enable
- a `.retrace` file
- extraction
- root PidFile replay
- the Go replay binary

Those paths belong to the Docker scenario harness.

## What Belongs Here

Add a dockertest scenario here when it can honestly be represented as a
same-process Python function. Good candidates are pure library exercises,
stdlib determinism checks, simple async functions, serialization, formatting,
and in-memory client mocks.

Keep a scenario out of this harness when its meaning depends on:

- subprocess or multiprocessing lifecycle
- `.pth` startup behavior
- CLI recording behavior
- extraction or PidFile replay
- a long-running server and separate client
- Docker services such as Postgres
- real socket/TLS server behavior
- background workers that outlive the function body

The guard test `test_out_of_process_scenarios_are_not_inprocess_cases` ensures
known Docker-first scenarios do not silently appear in the in-process scenario
table.

## Relationship To `dockertests/`

`dockertests/` is the end-to-end scenario harness. It should remain the source
of truth for user-visible record/extract/replay behavior.

This in-process pytest harness is a faster, narrower companion. It is useful
when a dockertest body can expose the same proxy/runtime divergence without the
noise of containers, packaging, recording files, or the replay binary.

See also:

- [`dockertests/README.md`](../dockertests/README.md)
- [`docs/internals/test-harnesses.md`](../docs/internals/test-harnesses.md)
- [`tests/AGENTS.md`](AGENTS.md)
