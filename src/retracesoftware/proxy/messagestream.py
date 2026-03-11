"""Linear message stream for record/replay.

The recording is a single flat sequence of tagged values::

    SYNC  RESULT value  SYNC  RESULT value2  SYNC  ERROR exc  ...

``MessageStream`` reads from any callable *source* that returns the
next value — ``stream.reader`` for file-backed traces, or
``iter(list).__next__`` for in-memory testing.

``MemoryWriter`` / ``MemoryReader`` are the in-memory backend:
the writer appends to a plain list, and the reader wraps a
``MessageStream`` around that list.

When multiple threads are active, ``MemoryWriter`` inserts
``ThreadSwitchMessage`` markers when the writing thread changes.
``MemoryReader`` demultiplexes the interleaved tape so each thread
reads only its own messages.
"""
import threading

import retracesoftware.utils as utils


# ── Message types ─────────────────────────────────────────────

class ResultMessage:
    __slots__ = ('result',)
    def __init__(self, result):
        self.result = result

class ErrorMessage:
    __slots__ = ('error',)
    def __init__(self, error):
        self.error = error

class CallMessage:
    __slots__ = ('args', 'kwargs')
    def __init__(self, args, kwargs):
        self.args = args
        self.kwargs = kwargs

class CheckpointMessage:
    __slots__ = ('value',)
    def __init__(self, value):
        self.value = value

class MonitorMessage:
    __slots__ = ('value',)
    def __init__(self, value):
        self.value = value

class ThreadSwitchMessage:
    """Marker written to the tape when the writing thread changes.

    During recording, ``MemoryWriter`` inserts one of these whenever
    a write comes from a different thread than the previous write.
    During replay, ``MemoryReader`` uses these to demultiplex the
    interleaved tape so each thread reads only its own messages.
    """
    __slots__ = ('thread_id',)
    def __init__(self, thread_id):
        self.thread_id = thread_id

    def __repr__(self):
        return f'ThreadSwitchMessage({self.thread_id!r})'


# ── Core reader ───────────────────────────────────────────────

class MessageStream:
    """Pull-based reader over a linear tagged message stream.

    Parameters
    ----------
    source : callable
        Returns the next value from the stream on each call.
        Works with ``stream.per_thread(...)`` for file-backed
        traces, or ``iter(tape).__next__`` for in-memory.
    """

    def __init__(self, source, monitor_enabled=False, native_reader=None):
        self.source = source
        self.type_deserializer = {}
        self._monitor_enabled = monitor_enabled
        self._native_reader = native_reader

    def bind(self, obj):
        if self._native_reader is None:
            raise RuntimeError("MessageStream.bind() requires a native reader")
        while not self._native_reader.pending_bind:
            self.source()
        return self._native_reader.bind(obj)

    def new_patched(self, obj):
        raise RuntimeError("MessageStream.new_patched() should not be used during replay")

    def read_result(self):
        value = self.result()
        deserializer = self.type_deserializer.get(type(value))
        return deserializer(value) if deserializer else value

    def _next_message(self):
        """Read and parse the next tagged message from the source."""
        tag = self.source()

        # Skip handle messages encountered after a PID switch in fork replay.
        while isinstance(tag, HandleMessage):
            tag = self.source()

        if tag == 'RESULT':
            return ResultMessage(self.source())
        elif tag == 'ERROR':
            return ErrorMessage(self.source())
        elif tag == 'CALL':
            return CallMessage(self.source(), self.source())
        elif tag == 'CHECKPOINT':
            return CheckpointMessage(self.source())
        elif tag == 'MONITOR':
            return MonitorMessage(self.source())
        else:
            # SYNC, or any other bare tag — return as-is
            return tag

    @utils.striptraceback
    def result(self):
        """Read the next result, skipping side-effect messages.

        Returns the value on RESULT, raises the exception on ERROR.
        CALL messages between SYNC and RESULT are consumed and
        discarded (during replay, callbacks re-execute naturally).

        The ``@striptraceback`` decorator is critical for correctness.
        When an ``ErrorMessage`` is re-raised here, Python attaches a
        traceback that references this frame.  That traceback holds a
        strong reference to the frame object, which in turn keeps all
        locals in the frame alive (including retrace internals).  This
        extends the lifetime of objects that would otherwise be
        garbage-collected, changing the order and timing of ``__del__``
        and weak-reference callbacks compared to the original recording.
        Because GC side-effects can trigger further patched calls,
        different GC timing causes replay divergence.

        ``striptraceback`` strips ``__traceback__``, ``__context__``,
        and ``__cause__`` from the exception at the C level before
        re-raising, so no extra frame references are retained and
        object lifetimes match the recording.
        """
        while True:
            msg = self._next_message()

            if isinstance(msg, ResultMessage):
                return msg.result
            elif isinstance(msg, ErrorMessage):
                raise msg.error
            elif isinstance(msg, MonitorMessage):
                if self._monitor_enabled:
                    from retracesoftware.install import ReplayDivergence
                    raise ReplayDivergence(
                        f"unexpected MONITOR({msg.value!r}) in result stream")
                continue
            elif isinstance(msg, (CallMessage, CheckpointMessage)):
                continue  # skip side-effects
            elif msg == 'SYNC':
                continue  # skip sync markers encountered mid-read
            else:
                continue  # skip unknown tags

    def sync(self):
        """Advance to the next SYNC marker.

        Consumes and discards everything until a bare ``'SYNC'``
        string is read from the source.  When monitoring is enabled,
        encountering a ``MonitorMessage`` is a divergence (the
        recording had function calls that replay did not replicate).
        """
        while True:
            msg = self._next_message()
            if msg == 'SYNC':
                return
            if isinstance(msg, MonitorMessage):
                if self._monitor_enabled:
                    from retracesoftware.install import ReplayDivergence
                    raise ReplayDivergence(
                        f"unexpected MONITOR({msg.value!r}) during sync "
                        f"— recording had function calls that replay did not")
                continue

    def checkpoint(self, value):
        """Read the next CHECKPOINT and compare against *value*.

        Raises ``ReplayDivergence`` if the stored checkpoint does
        not match *value*.  Skips intervening side-effect messages.
        """
        while True:
            msg = self._next_message()
            if isinstance(msg, CheckpointMessage):
                if value != msg.value:
                    from retracesoftware.install import ReplayDivergence
                    raise ReplayDivergence(
                        f"replay divergence: expected {msg.value!r}, "
                        f"got {value!r}")
                return
            elif isinstance(msg, MonitorMessage):
                if self._monitor_enabled:
                    from retracesoftware.install import ReplayDivergence
                    raise ReplayDivergence(
                        f"unexpected MONITOR({msg.value!r}) during checkpoint")
                continue
            elif isinstance(msg, (CallMessage, ResultMessage, ErrorMessage)):
                continue
            elif msg == 'SYNC':
                continue

    def monitor_checkpoint(self, value):
        """Read the next MONITOR message and compare against *value*.

        Used during replay when monitoring is enabled.  Reads the
        very next message — does NOT skip other message types.  If
        the next message is not a ``MonitorMessage``, or its value
        differs, raises ``ReplayDivergence``.
        """
        msg = self._next_message()
        if not isinstance(msg, MonitorMessage):
            from retracesoftware.install import ReplayDivergence
            raise ReplayDivergence(
                f"expected MONITOR({value!r}), got {type(msg).__name__} "
                f"— replay has function calls that recording did not")
        if value != msg.value:
            from retracesoftware.install import ReplayDivergence
            raise ReplayDivergence(
                f"monitor divergence: expected {msg.value!r}, "
                f"got {value!r}")


# ── In-memory writer ──────────────────────────────────────────

class HandleMessage:
    """A named handle write on the tape.

    Handle messages carry side-effect data (e.g. stack deltas) that
    the reader skips during replay.  They are written as
    ``HandleMessage(name, value)`` on the tape.
    """
    __slots__ = ('name', 'value')
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def __repr__(self):
        return f'HandleMessage({self.name!r}, {self.value!r})'


class MemoryWriter:
    """Write tagged messages to an in-memory list.

    The resulting ``tape`` is a flat list of values in the same
    tagged format that ``stream.writer`` produces, so
    ``MessageStream`` can consume it directly.

    Parameters
    ----------
    stackfactory : utils.StackFactory or None
        When provided, ``stacktrace()`` captures a stack delta
        and appends it as a ``HandleMessage('STACKTRACE', delta)``
        on the tape.
    thread : callable or None
        When provided, called before every write to detect thread
        switches.  Should return a thread identifier (e.g.
        ``threading.get_ident``).  When the thread changes between
        writes, a ``ThreadSwitchMessage`` marker is inserted.

    Attributes
    ----------
    tape : list
        The recorded message stream.
    """

    __slots__ = ('tape', '_stackfactory', '_thread', '_last_thread', 'type_serializer')

    def __init__(self, stackfactory = None, thread = None):
        self.tape = []
        self._stackfactory = stackfactory
        self._thread = thread
        self._last_thread = thread() if thread else None
        self.type_serializer = {}

    def _maybe_switch(self):
        """Insert a ThreadSwitchMessage if the current thread differs."""
        if self._thread is not None:
            tid = self._thread()
            if tid != self._last_thread:
                self._last_thread = tid
                self.tape.append(ThreadSwitchMessage(tid))

    def sync(self):
        self._maybe_switch()
        self.tape.append('SYNC')

    def write_result(self, value):
        self._maybe_switch()
        self.tape.append('RESULT')
        serializer = self.type_serializer.get(type(value))
        self.tape.append(serializer(value) if serializer else value)

    def write_error(self, exc_type, exc_value, exc_tb):
        self._maybe_switch()
        self.tape.append('ERROR')
        self.tape.append(exc_value)

    def write_call(self, *args, **kwargs):
        self._maybe_switch()
        self.tape.append('CALL')
        self.tape.append(args)
        self.tape.append(kwargs)

    def checkpoint(self, value):
        self._maybe_switch()
        self.tape.append('CHECKPOINT')
        self.tape.append(value)

    def monitor_event(self, value):
        self._maybe_switch()
        self.tape.append('MONITOR')
        self.tape.append(value)

    def stacktrace(self):
        """Record a stack trace at the current call boundary.

        If a ``stackfactory`` was provided at construction, captures
        a stack delta and appends it as a ``HandleMessage`` on the
        tape.  Otherwise does nothing.
        """
        if self._stackfactory is not None:
            self.tape.append(HandleMessage('STACKTRACE', self._stackfactory.delta()))

    def handle(self, name):
        """Return a callable that writes a named handle to the tape.

        This mirrors ``stream.writer.handle(name)`` — the returned
        callable accepts a single value and appends a ``HandleMessage``
        to the tape.  Handle messages are side-effect data (e.g. stack
        trace deltas) that the reader skips during replay.

        Parameters
        ----------
        name : str
            The handle name (e.g. ``'STACKTRACE'``).

        Returns
        -------
        callable
            ``handle_writer(value)`` — appends ``HandleMessage(name, value)``
            to the tape.
        """
        tape = self.tape
        def handle_writer(value):
            tape.append(HandleMessage(name, value))
        return handle_writer

    def bind(self, *a, **kw):
        pass

    def new_patched(self, obj):
        return self.bind(obj)

    def ext_bind(self, *a, **kw):
        return self.bind(*a, **kw)

    def reader(self):
        """Create a ``MemoryReader`` from the recorded tape."""
        return MemoryReader(self.tape)


# ── In-memory reader ──────────────────────────────────────────

class MemoryReader:
    """Read from an in-memory tape via ``MessageStream``.

    Implements the reader interface expected by
    ``System.replay_context``.

    When the tape contains ``ThreadSwitchMessage`` markers (written by
    a thread-aware ``MemoryWriter``), the reader demultiplexes the
    interleaved tape so each thread reads only its own messages.
    A shared cursor walks the tape sequentially; each thread blocks
    until the cursor reaches a segment that belongs to it.

    Parameters
    ----------
    tape : list
        A flat list produced by ``MemoryWriter``.
    """

    def __init__(self, tape, timeout=None, monitor_enabled=False):
        self.type_deserializer = {}
        self._monitor_enabled = monitor_enabled
        self._tape = tape
        self._tape_len = len(tape)

        has_threads = any(isinstance(v, ThreadSwitchMessage) for v in tape)

        if has_threads:
            self._demux = _TapeDemux(tape, timeout=timeout,
                                     monitor_enabled=monitor_enabled)
            self._stream = None  # per-thread streams managed by demux
            self._tape_iter = None
        else:
            self._demux = None
            self._tape_iter = iter(tape)
            self._stream = MessageStream(self._tape_iter.__next__,
                                         monitor_enabled=monitor_enabled)

    def _get_stream(self):
        if self._demux is not None:
            return self._demux.stream_for_current_thread()
        return self._stream

    def sync(self):
        self._get_stream().sync()

    def read_result(self):
        result = self._get_stream().result()
        deserializer = self.type_deserializer.get(type(result))
        if deserializer:
            return deserializer(result)
        return result

    def bind(self, *a, **kw):
        pass

    def checkpoint(self, value):
        self._get_stream().checkpoint(value)

    def monitor_checkpoint(self, value):
        self._get_stream().monitor_checkpoint(value)

    @property
    def remaining(self):
        """Number of unconsumed tape entries (single-thread tapes only)."""
        if self._tape_iter is None:
            return 0
        rest = list(self._tape_iter)
        return len(rest)


# ── Tape demultiplexer ────────────────────────────────────────

class _TapeDemux:
    """Demultiplex an interleaved tape by thread.

    The tape is a flat list with ``ThreadSwitchMessage`` markers
    indicating which thread owns the subsequent messages.  This
    class provides a blocking ``source()`` callable per thread:
    each thread waits until the shared cursor reaches a segment
    that belongs to it, then reads values until the next thread
    switch.

    Under CPython's GIL, threads interleave deterministically
    (modulo timing), and the tape captures the exact interleaving
    from the recording.  During replay, threads block on their
    ``Event`` until the demuxer activates them, reproducing the
    same interleaving.
    """

    def __init__(self, tape, timeout=None, monitor_enabled=False):
        self._tape = tape
        self._pos = 0
        self._lock = threading.Lock()
        self._streams = {}
        self._events = {}
        self._timeout = timeout
        self._monitor_enabled = monitor_enabled

        # Determine the initial thread from the first ThreadSwitchMessage
        self._current_thread = None
        for item in tape:
            if isinstance(item, ThreadSwitchMessage):
                self._current_thread = item.thread_id
                break

    def _get_event(self, tid):
        """Get or create the Event for a thread."""
        if tid not in self._events:
            self._events[tid] = threading.Event()
        return self._events[tid]

    def stream_for_current_thread(self):
        """Return a ``MessageStream`` for the calling thread."""
        tid = threading.get_ident()
        if tid not in self._streams:
            self._streams[tid] = MessageStream(
                self._make_source(tid),
                monitor_enabled=self._monitor_enabled)
        return self._streams[tid]

    def _make_source(self, tid):
        """Return a callable that yields values for thread *tid*."""
        def source():
            while True:
                with self._lock:
                    # If this thread is the active one and there are
                    # values to read, consume the next one.
                    if self._current_thread == tid and self._pos < len(self._tape):
                        item = self._tape[self._pos]
                        self._pos += 1

                        if isinstance(item, ThreadSwitchMessage):
                            # Switch to a different thread — wake it,
                            # then block ourselves.
                            self._current_thread = item.thread_id
                            self._get_event(item.thread_id).set()
                            continue  # retry — we consumed a marker, not data

                        return item

                # Not our turn — wait for activation.
                event = self._get_event(tid)
                if not event.wait(timeout=self._timeout):
                    raise TimeoutError(
                        f"replay demux timed out after {self._timeout}s "
                        f"waiting for thread {tid} "
                        f"(tape pos {self._pos}/{len(self._tape)}, "
                        f"active thread {self._current_thread})"
                    )
                event.clear()

        return source
