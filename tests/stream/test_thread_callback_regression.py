"""Regression for record startup failure: `thread must be callable`.

Root symptom in record mode:
    TypeError: thread must be callable

Root cause:
- `retracesoftware.__main__.py` passes `utils.ThreadLocal()` into
  `stream.writer(..., thread=thread_id, ...)`.
- `stream.writer` forwards that value into native `Queue(thread=...)`.
- native `Queue` now validates `thread` as callable, but
  `utils.ThreadLocal()` is not callable (it exposes `.get()` / `.set()`).

This test keeps the reproducer component-local (`stream` + `utils`) and
avoids dockertests/application layers.
"""

import pytest

pytest.importorskip("retracesoftware.stream")
pytest.importorskip("retracesoftware.utils")

import retracesoftware.stream as stream
import retracesoftware.utils as utils


def test_writer_accepts_threadlocal_from_runtime_context(tmp_path):
    """`stream.writer` should accept the runtime ThreadLocal object.

    The top-level record path uses `utils.ThreadLocal()` for thread identity.
    Creating a writer with that same object should not fail at queue init.
    """

    thread_ctx = utils.ThreadLocal()
    thread_ctx.set(("main",))

    path = tmp_path / "thread_ctx_trace.bin"
    with stream.writer(path=path, thread=thread_ctx, flush_interval=0.01, raw=True) as writer:
        writer("ok")
        writer.flush()
