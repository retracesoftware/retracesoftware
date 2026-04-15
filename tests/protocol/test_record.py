from retracesoftware.install import stream_writer


class RecordingWriter:
    def __init__(self):
        self.type_serializer = {}
        self.calls = []

    def handle(self, name):
        def emit(*args):
            self.calls.append((name, args))
        return emit

    def bind(self, obj):
        self.calls.append(("bind", (obj,)))

    def intern(self, obj):
        self.calls.append(("intern", (obj,)))

    def async_new_patched(self, obj):
        self.calls.append(("ASYNC_NEW_PATCHED", (obj,)))


class PrivateInternWriter:
    def __init__(self):
        self.type_serializer = {}
        self.calls = []

    def handle(self, name):
        def emit(*args):
            self.calls.append((name, args))
        return emit

    def bind(self, obj):
        self.calls.append(("bind", (obj,)))

    def _intern(self, obj):
        self.calls.append(("_intern", (obj,)))

    def async_new_patched(self, obj):
        self.calls.append(("ASYNC_NEW_PATCHED", (obj,)))


def test_stream_writer_async_call_serializes_kwargs_as_payloads():
    raw_writer = RecordingWriter()
    writer = stream_writer(raw_writer)

    writer.async_call("socket", fileno=123, family=2)

    assert raw_writer.calls == [
        ("ASYNC_CALL", ("socket", (), {"fileno": 123, "family": 2})),
    ]


def test_stream_writer_supports_no_stackfactory():
    raw_writer = RecordingWriter()
    writer = stream_writer(raw_writer)

    assert writer.stacktrace is None

    writer.checkpoint({"x": 1})

    assert raw_writer.calls == [
        ("CHECKPOINT", ({"x": 1},)),
    ]


def test_stream_writer_uses_private_intern_fallback():
    raw_writer = PrivateInternWriter()
    writer = stream_writer(raw_writer)

    writer.intern("CALL")

    assert raw_writer.calls == [
        ("_intern", ("CALL",)),
    ]
