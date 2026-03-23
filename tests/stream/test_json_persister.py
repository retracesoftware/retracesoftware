import json
import subprocess
import sys

import pytest

pytest.importorskip("retracesoftware.stream")
import retracesoftware.stream as stream


def _disable_heartbeat(monkeypatch):
    monkeypatch.setattr(stream, "call_periodically", lambda interval, func: None)


def _read_json_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _record_thread_lock_trace(tmp_path, *, factory_expr, script_name, trace_name):
    script_path = tmp_path / script_name
    script_path.write_text(
        f"""\
import _thread

lock = {factory_expr}
assert lock.acquire() is True
lock.release()
assert lock.acquire(False) is True
lock.release()
""",
        encoding="utf-8",
    )
    trace_path = tmp_path / trace_name

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(trace_path),
            "--format",
            "json",
            "--",
            str(script_path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"record failed (exit {result.returncode}):\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

    return _read_json_lines(trace_path)


def _assert_thread_lock_proxy_events(events, *, expect_stubref):
    serialized_stubrefs = [
        event
        for event in events
        if event["event"] == "object"
        and isinstance(event.get("value"), dict)
        and event["value"].get("kind") == "serialized"
        and isinstance(event["value"].get("value"), dict)
        and event["value"]["value"].get("type") == "retracesoftware.proxy.stubfactory.StubRef"
    ]
    true_results = [event for event in events if event["event"] == "object" and event.get("value") is True]
    none_results = [event for event in events if event["event"] == "object" and event.get("value") is None]

    if expect_stubref:
        assert serialized_stubrefs, "expected proxied _thread lock allocation to record a serialized StubRef"
    assert len(true_results) >= 2, "expected acquire() calls to record True results"
    assert len(none_results) >= 2, "expected release() calls to record None results"


def test_writer_format_json_uses_python_json_persister(monkeypatch, tmp_path):
    _disable_heartbeat(monkeypatch)
    trace_path = tmp_path / "trace.jsonl"

    with stream.writer(
        path=trace_path,
        format="json",
        flush_interval=999,
        preamble={"type": "exec"},
    ) as writer:
        assert isinstance(writer.queue.persister, stream.JsonPersister)
        handle = writer.handle("PAYLOAD")
        handle()
        writer([1, 2])
        writer(b"bytes")
        writer.flush()

    events = _read_json_lines(trace_path)

    assert events[0]["event"] == "process_info"
    assert any(event["event"] == "intern" and event["value"] == "PAYLOAD" for event in events)
    assert any(event["event"] == "bound_ref" for event in events)
    assert any(
        event["event"] == "start_collection"
        and event["collection_type"] == "list"
        and event["length"] == 2
        for event in events
    )
    assert any(event["event"] == "object" and event["value"] == 1 for event in events)
    assert any(event["event"] == "object" and event["value"] == 2 for event in events)
    assert any(
        event["event"] == "object"
        and isinstance(event["value"], dict)
        and event["value"].get("kind") == "bytes"
        and event["value"].get("encoding") == "base64"
        for event in events
    )


def test_writer_output_parameter_still_bypasses_format_selector(monkeypatch):
    _disable_heartbeat(monkeypatch)

    class RecordingPersister:
        def __init__(self):
            self.events = []

        def write_object(self, obj):
            self.events.append(("object", obj))

        def flush(self):
            self.events.append(("flush",))

        def shutdown(self):
            self.events.append(("shutdown",))

    persister = RecordingPersister()

    with stream.writer(output=persister, format="json", flush_interval=999) as writer:
        writer("wrapped-output")
        writer.flush()

    assert ("object", "wrapped-output") in persister.events
    assert ("flush",) in persister.events


def test_json_persister_records_thread_lock_proxy_events(tmp_path):
    events = _record_thread_lock_trace(
        tmp_path,
        factory_expr="_thread.allocate_lock()",
        script_name="thread_lock_script.py",
        trace_name="thread_lock_trace.jsonl",
    )
    _assert_thread_lock_proxy_events(events, expect_stubref=True)

def test_json_persister_records_thread_rlock_proxy_events(tmp_path):
    events = _record_thread_lock_trace(
        tmp_path,
        factory_expr="_thread.RLock()",
        script_name="thread_rlock_script.py",
        trace_name="thread_rlock_trace.jsonl",
    )
    _assert_thread_lock_proxy_events(events, expect_stubref=False)
