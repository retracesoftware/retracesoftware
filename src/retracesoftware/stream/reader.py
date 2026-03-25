"""Replay reader stack.

This module keeps the reader pipeline split into small layers:

- ``HeartbeatReader`` strips heartbeat control messages while remembering the
  most recent one.
- ``WithThreadReader`` converts a flat tape stream plus ``ThreadSwitch``
  markers into ``(thread_id, obj)`` tuples.
- ``PeekableReader`` buffers future ``(thread_id, obj)`` tuples.
- ``DemuxReader`` routes those tuples by thread.
- ``ResolvingReader`` resolves ``BindingLookup`` records against a live
  binding table and consumes ``BindingDelete`` records internally.
- ``ObjectReader`` wires the layers together into the public end-of-chain
  reader counterpart to ``ObjectWriter``.
"""

from collections import deque

import retracesoftware.functional as functional
import retracesoftware.utils as utils

from . import (
    BindingCreate,
    BindingDelete,
    BindingLookup,
    Heartbeat,
    ThreadSwitch,
)

_MISSING = object()


class ExpectedBindingCreate(RuntimeError):
    __slots__ = ["next"]

    def __init__(self, next):
        super().__init__("Expected BindingCreate")
        self.next = next


class HeartbeatReader:
    """Strip heartbeat control messages from a flat tape stream."""

    __slots__ = ["source", "last_heartbeat"]

    def __init__(self, source):
        self.source = source
        self.last_heartbeat = None

    def __call__(self):
        return self.next()

    def next(self):
        while True:
            item = self.source()
            if isinstance(item, Heartbeat):
                self.last_heartbeat = item
                continue
            return item

    def close(self):
        close = getattr(self.source, "close", None)
        if close is not None:
            return close()


class WithThreadReader:
    """Attach the current thread id to plain tape objects.

    The wrapped source emits plain record-domain objects, including
    ``ThreadSwitch`` markers. This adapter consumes those markers and yields
    ``(thread_id, obj)`` tuples for the current thread.
    """

    __slots__ = ["source", "thread_id"]

    def __init__(self, source, initial_thread_id = None):
        self.source = source
        self.thread_id = initial_thread_id

    def __call__(self):
        return self.next()

    def next(self):
        while True:
            item = self.source()
            if isinstance(item, ThreadSwitch):
                if item.value is not None:
                    self.thread_id = item.value
                continue
            return (self.thread_id, item)

    def close(self):
        close = getattr(self.source, "close", None)
        if close is not None:
            return close()


class PeekableReader:
    __slots__ = ["source", "_buffer"]

    def __init__(self, source):
        self.source = source
        self._buffer = deque()

    def __call__(self):
        return self.next()

    def __iter__(self):
        return self

    def __next__(self):
        return self.next()

    def next(self):
        if self._buffer:
            return self._buffer.popleft()
        return self.source()

    def peek(self, thread_id_or_predicate, predicate=None):
        if predicate is None and callable(thread_id_or_predicate):
            predicate = thread_id_or_predicate
        else:
            thread_id = thread_id_or_predicate
            value_predicate = predicate
            if value_predicate is None:
                def predicate(item):
                    return item[0] == thread_id
            else:
                def predicate(item):
                    return item[0] == thread_id and value_predicate(item[1])

        for item in self._buffer:
            if predicate(item):
                return item

        while True:
            item = self.source()
            self._buffer.append(item)
            if predicate(item):
                return item

    def close(self):
        close = getattr(self.source, "close", None)
        if close is not None:
            return close()


class DemuxReader:
    """Thread-scoped view over a shared tuple source."""

    __slots__ = ["thread_id", "dispatcher"]

    def __init__(self, source, thread_id=lambda: None):
        self.thread_id = thread_id
        self.dispatcher = utils.Dispatcher(source)

    @property
    def source(self):
        return self.dispatcher.source

    def __call__(self, thread_id=_MISSING):
        return self.next(thread_id)

    def next(self, thread_id=_MISSING):
        if thread_id is _MISSING:
            item = self.dispatcher.next(
                lambda item: item[0] == self.thread_id())
        else:
            item = self.dispatcher.next(
                lambda item: item[0] == thread_id)
        return item[1]

    def pending(self, thread_id):
        item = self.dispatcher.buffered
        if item[0] != thread_id:
            raise KeyError(thread_id)
        return item[1]

    def peek(self, thread_id=_MISSING, predicate=None):
        predicate = (lambda value: True) if predicate is None else predicate

        try:
            current_thread_id = self.thread_id() if thread_id is _MISSING else thread_id
            value = self.pending(current_thread_id)
        except KeyError:
            pass
        else:
            if predicate(value):
                return value

        if thread_id is _MISSING:
            return self.source.peek(
                lambda item: item[0] == self.thread_id() and predicate(item[1]))[1]
        return self.source.peek(
            lambda item: item[0] == thread_id and predicate(item[1]))[1]

    @property
    def buffered(self):
        try:
            return self.dispatcher.buffered[1]
        except Exception:
            return None

    @property
    def waiting_thread_count(self):
        return self.dispatcher.waiting_thread_count

    def wait_for_all_pending(self):
        return self.dispatcher.wait_for_all_pending()

    def interrupt(self, on_waiting_thread, while_interrupted):
        return self.dispatcher.interrupt(on_waiting_thread, while_interrupted)

    def close(self):
        close = getattr(self.source, "close", None)
        if close is not None:
            return close()


class ResolvingReader:
    """Resolve binding records on top of a demultiplexed source.

    ``next()`` returns visible replay objects for the current thread.
    ``BindingDelete`` records are consumed internally. ``BindingCreate`` is not
    returned here; instead callers are expected to satisfy the next create
    record by calling ``bind(obj)``.
    """

    __slots__ = ["source", "_bindings"]

    def __init__(self, source):
        self.source = source
        self._bindings = {}

    def __call__(self, thread_id=_MISSING):
        return self.next(thread_id)

    def _resolve(self, obj, bindings=None):
        if bindings is None:
            bindings = self._bindings

        def transform(value):
            if isinstance(value, BindingLookup):
                return bindings[value.index]
            return value

        resolver = functional.walker(transform)
        return resolver(obj)

    def _consume_deletes(self):
        """Advance past delete records while keeping the binding table in sync."""
        next = self.source()

        while isinstance(next, BindingDelete):
            self._bindings.pop(next.index, None)
            next = self.source()

        return next

    def next(self, thread_id=_MISSING):
        """Return the next resolved replay object.

        The next visible record must not be ``BindingCreate``; those are handled
        through ``bind(obj)``.
        """
        next = self._consume_deletes()

        if isinstance(next, BindingCreate) and thread_id is _MISSING:
            raise RuntimeError("BindingCreate returned when bind was expected")

        return self._resolve(next)

    def bind(self, obj):
        """Consume the next ``BindingCreate`` record and bind it to ``obj``.

        This method is intentionally stateful: it advances the underlying
        source and expects the next visible message to be ``BindingCreate``.
        """
        next = self._consume_deletes()

        if not isinstance(next, BindingCreate):
            raise ExpectedBindingCreate(next)

        self._bindings[next.index] = obj

    def _peek_visible(self):
        shadow_bindings = dict(self._bindings)
        demux = self.source
        thread_id = demux.thread_id()
        peekable = demux.source
        index = 0

        current = demux.dispatcher.buffered
        if current[0] == thread_id:
            pending = [current]
        else:
            pending = []

        while True:
            if pending:
                item = pending.pop(0)
            else:
                while index >= len(peekable._buffer):
                    peekable._buffer.append(peekable.source())
                item = peekable._buffer[index]
                index += 1

            if item[0] != thread_id:
                continue

            value = item[1]
            if isinstance(value, BindingDelete):
                shadow_bindings.pop(value.index, None)
                continue
            return value, shadow_bindings

    def peek(self, thread_id=None):
        value, shadow_bindings = self._peek_visible()
        if isinstance(value, BindingCreate):
            raise RuntimeError("BindingCreate returned when bind was expected")
        return self._resolve(value, shadow_bindings)

    @property
    def waiting_thread_count(self):
        return self.source.waiting_thread_count

    def wait_for_all_pending(self):
        return self.source.wait_for_all_pending()

    def interrupt(self, on_waiting_thread, while_interrupted):
        return self.source.interrupt(on_waiting_thread, while_interrupted)

    def close(self):
        close = getattr(self.source, "close", None)
        if close is not None:
            return close()


class ObjectReader:
    """Public replay reader built from the full reader stack."""

    __slots__ = ["source"]

    def __init__(self, thread_id, source):
        heartbeat_reader = HeartbeatReader(source)
        initial_thread_id = None if thread_id is None else thread_id()
        with_thread_reader = WithThreadReader(heartbeat_reader, initial_thread_id)
        if thread_id is None:
            def thread_id():
                return with_thread_reader.thread_id
        
        demux_reader = DemuxReader(
            source = PeekableReader(with_thread_reader),
            thread_id = thread_id,
        )
        self.source = ResolvingReader(demux_reader)

    def __call__(self, thread_id=_MISSING):
        return self.next(thread_id)

    @property
    def resolving_reader(self):
        return self.source

    @property
    def demux_reader(self):
        return self.resolving_reader.source

    @property
    def peekable_reader(self):
        return self.demux_reader.source

    @property
    def with_thread_reader(self):
        return self.peekable_reader.source

    @property
    def heartbeat_reader(self):
        return self.with_thread_reader.source

    @property
    def last_heartbeat(self):
        return self.heartbeat_reader.last_heartbeat

    def peek(self, thread_id=None):
        return self.resolving_reader.peek()

    def next(self, thread_id=_MISSING):
        return self.resolving_reader.next(thread_id)

    def bind(self, obj):
        self.resolving_reader.bind(obj)

    @property
    def waiting_thread_count(self):
        return self.source.waiting_thread_count

    def wait_for_all_pending(self):
        return self.source.wait_for_all_pending()

    def interrupt(self, on_waiting_thread, while_interrupted):
        return self.source.interrupt(on_waiting_thread, while_interrupted)

    def close(self):
        close = getattr(self.source, "close", None)
        if close is not None:
            return close()
