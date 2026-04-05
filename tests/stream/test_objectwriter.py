import pytest

pytest.importorskip("retracesoftware.stream")

import retracesoftware.stream as stream


class RecordingQueue:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        if not name.startswith("push_"):
            raise AttributeError(name)

        def push(*args):
            self.calls.append((name, args))
            return True

        return push


def test_objectwriter_write_uses_python_queue_fallback_and_serializer():
    queue = RecordingQueue()
    writer = stream.ObjectWriter(queue, lambda obj: ("serialized", obj))

    writer.write("hello", 123)

    assert queue.calls == [
        ("push_obj", (("serialized", "hello"),)),
        ("push_obj", (("serialized", 123),)),
    ]
