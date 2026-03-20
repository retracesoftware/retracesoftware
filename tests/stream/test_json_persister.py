import json

import pytest

pytest.importorskip("retracesoftware.stream")
import retracesoftware.stream as stream


def _disable_heartbeat(monkeypatch):
    monkeypatch.setattr(stream, "call_periodically", lambda interval, func: None)


def _read_json_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


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
