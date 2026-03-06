"""Regression tests for backpressure hangs in retracesoftware.stream.

This file contains a *minimal reproducer* for the observed stall: under
extreme backpressure, persister-side processing can wait for additional
compound-value entries while holding the GIL, which can block producers from
making progress.
"""

import importlib.util
import multiprocessing as mp
from pathlib import Path
import traceback

import pytest


def _load_local_stream_module():
    """Load stream wrapper from this repository checkout.

    This avoids accidentally importing an older site-packages wrapper that does
    not expose current backpressure knobs.
    """
    stream_init = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "retracesoftware"
        / "stream"
        / "__init__.py"
    )
    spec = importlib.util.spec_from_file_location(
        "retracesoftware_stream_local_test",
        str(stream_init),
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load stream module from {stream_init}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _writer_worker(path: str, status_q):
    """Run a minimal recording workload that historically can hang.

    The payload is a one-item dict. Dict serialization writes a CMD_DICT marker
    and then key/value entries. With very low inflight/queue limits, producer
    and persister can enter a no-progress state if the persister waits for the
    next nested value while still holding the GIL.
    """
    try:
        stream = _load_local_stream_module()
        with stream.writer(
            path=path,
            thread=lambda: 1,
            flush_interval=0.01,
            inflight_limit=1,
            stall_timeout=1,
            queue_capacity=2,
            return_queue_capacity=2,
            verbose=False,
        ) as w:
            key = "k" * 4096
            value = "v" * 262144
            w({key: value})
            w.flush()
        status_q.put(("ok", None))
    except Exception:
        status_q.put(("err", traceback.format_exc()))


def test_extreme_backpressure_dict_recording_does_not_hang(tmp_path):
    """Regression: writer must not hang under extreme backpressure.

    This is intentionally strict:
    - if the child process is still alive after timeout, we treat it as the
      reproduced hang and fail the test
    - after the stream fix lands, this test should consistently complete
    """
    try:
        stream = _load_local_stream_module()
    except ImportError as exc:
        pytest.skip(f"stream extension not importable in test env: {exc}")

    for required in ("inflight_limit", "stall_timeout", "queue_capacity"):
        if required not in stream.writer.__init__.__code__.co_varnames:
            pytest.fail(f"local stream.writer missing expected argument: {required}")
    for required_api in ("FramedWriter", "AsyncFilePersister"):
        if not hasattr(stream._backend_mod, required_api):
            pytest.fail(
                f"stream backend missing required API for recording path: {required_api}"
            )

    ctx = mp.get_context("spawn")
    status_q = ctx.Queue()
    out_path = str(tmp_path / "hang_repro_trace.bin")
    proc = ctx.Process(target=_writer_worker, args=(out_path, status_q))
    proc.start()
    proc.join(timeout=8)

    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=2)
        pytest.fail(
            "stream writer worker hung under extreme backpressure "
            "(reproduced no-progress condition)"
        )

    assert proc.exitcode == 0, f"writer worker crashed, exitcode={proc.exitcode}"
    status, payload = status_q.get(timeout=1)
    assert status == "ok", payload
