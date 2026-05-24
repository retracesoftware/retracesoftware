import json

import pytest

pytest.importorskip("retracesoftware.stream")
import retracesoftware.stream as stream


def _read_json_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_writer_format_json_uses_python_json_persister(tmp_path):
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


def test_writer_output_parameter_still_bypasses_format_selector():

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


def test_json_persister_serializes_binding_as_handle_ref(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    binding = stream.Binding(7)

    persister = stream.JsonPersister(trace_path)
    try:
        persister.write_object(binding)
        persister.flush()
    finally:
        persister.close()

    events = _read_json_lines(trace_path)

    assert events == [{"event": "handle_ref", "ref": 7}]


def test_json_persister_preserves_nested_binding_values(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    binding = stream.Binding(7)

    persister = stream.JsonPersister(trace_path)
    try:
        persister.write_object(("callable", binding))
        persister.flush()
    finally:
        persister.close()

    events = _read_json_lines(trace_path)

    assert events == [
        {
            "event": "object",
            "value": {
                "kind": "tuple",
                "items": [
                    "callable",
                    {"kind": "binding", "index": 7},
                ],
            },
        }
    ]
