"""
Tests for record/replay contexts built on top of ``System``.

Verifies that record/replay contexts correctly manage gate lifecycle
and route external calls through the pipeline.
"""
import os
import io
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import retracesoftware.utils as utils
import retracesoftware.stream as stream
from retracesoftware.protocol import StacktraceMessage
import retracesoftware.proxy.context as context_mod
from retracesoftware.proxy.contexts import record_context, replay_context
from retracesoftware.proxy.context import CallHooks, LifecycleHooks
from retracesoftware.proxy._system_context import _GateContext, Handler
from retracesoftware.proxy._system_specs import create_context
from retracesoftware.proxy.system import System
from retracesoftware.testing.memorytape import MemoryWriter, MemoryReader
pytestmark = pytest.mark.skip(reason="stale proxy.contexts coverage targets a deprecated System context surface")

_PATCHED_TYPE_KEEPALIVE = []


@pytest.fixture(autouse=True)
def keep_patched_test_types_alive(monkeypatch):
    """Prevent local patched class addresses from being reused across tests."""
    original = System.patch_type

    def patch_type_and_keepalive(self, cls):
        _PATCHED_TYPE_KEEPALIVE.append(cls)
        return original(self, cls)

    monkeypatch.setattr(System, "patch_type", patch_type_and_keepalive)


def test_system_record_replay_context():
    """record_context and replay_context activate/deactivate the sandbox."""
    system = System()
    w = MemoryWriter()

    # Before any context: sandbox is inactive
    assert not system._in_sandbox()
    assert not system._out_sandbox()

    with record_context(system, w):
        assert system._in_sandbox()
        assert system._out_sandbox()

    # After record context: gates restored
    assert not system._in_sandbox()
    assert not system._out_sandbox()

    with replay_context(system, w.reader()):
        assert system._in_sandbox()
        assert system._out_sandbox()

    # After replay context: gates restored
    assert not system._in_sandbox()
    assert not system._out_sandbox()


def test_record_and_replay_contexts_are_context_managers():
    system = System()
    writer = MemoryWriter()

    with record_context(system, writer):
        assert system._in_sandbox()

    with replay_context(system, writer.reader()):
        assert system._in_sandbox()


def test_context_on_bind_hook_sees_future_binds_only():
    system = System()
    seen = []

    class Thing:
        pass

    first = Thing()
    second = Thing()
    third = Thing()

    system.bind(first)
    system.bind(first)

    with system.context(
        internal_hooks=CallHooks(),
        external_hooks=CallHooks(),
        lifecycle_hooks=LifecycleHooks(),
        on_bind=seen.append,
    ):
        system.bind(second)
    system.bind(third)

    assert seen == [second]


def test_gate_context_restores_current_context_per_thread():
    system = System()
    context = _GateContext(system, internal=Handler(utils.noop))

    main_parent = object()
    child_parent = object()
    child_entered = threading.Event()
    allow_child_exit = threading.Event()
    child_restored = {}

    def child():
        system.current_context.set(child_parent)
        with context:
            assert system.current_context.get() is context
            child_entered.set()
            assert allow_child_exit.wait(timeout=1)
        child_restored["value"] = system.current_context.get()

    system.current_context.set(main_parent)

    with context:
        assert system.current_context.get() is context
        thread = threading.Thread(target=child)
        thread.start()
        assert child_entered.wait(timeout=1)

    assert system.current_context.get() is main_parent
    allow_child_exit.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert child_restored["value"] is child_parent


def test_gate_context_runs_on_start_and_on_end_around_installed_handlers():
    system = System()
    events = []

    def on_start():
        events.append(("start", system._internal.executor))

    def on_end():
        events.append(("end", system._internal.executor))

    context = _GateContext(
        system,
        internal=Handler(utils.noop),
        on_start=on_start,
        on_end=on_end,
    )

    with context:
        events.append(("body", system._internal.executor))

    events.append(("after", system._internal.executor))

    assert events == [
        ("start", utils.noop),
        ("body", utils.noop),
        ("end", utils.noop),
        ("after", None),
    ]


def test_system_active_allocations_bind_after_async_new_patched_outside_sandbox():
    """Inside retrace but outside the sandbox, allocations emit async_new_patched then bind."""
    system = System()

    class Patched:
        pass

    system.patch_type(Patched)

    events = []

    def bind_event(obj):
        events.append(("bind", obj))

    bind = utils.runall(system.is_bound.add, bind_event)

    def on_async_event(obj):
        events.append(("async_new_patched", obj))

    on_async_new_patched = utils.runall(on_async_event, bind)

    with _GateContext(
        system,
        bind=bind,
        async_new_patched=on_async_new_patched,
        external=Handler(utils.noop),
    ):
        external_obj = Patched()

    assert events == [("bind", external_obj)]
    assert system.is_bound(external_obj)

    events.clear()

    with _GateContext(
        system,
        bind=bind,
        async_new_patched=on_async_new_patched,
        internal=Handler(utils.noop),
    ):
        internal_obj = Patched()

    assert events == [
        ("async_new_patched", internal_obj),
        ("bind", internal_obj),
    ]
    assert system.is_bound(internal_obj)


def test_patch_type_binds_existing_and_future_subclasses():
    system = System()

    class Base:
        pass

    class Existing(Base):
        pass

    system.patch_type(Base)

    class Future(Base):
        pass

    assert system.is_bound(Base)
    assert system.is_bound(Existing)
    assert system.is_bound(Future)


def test_patch_type_leaves_subclass_only_methods_as_plain_python():
    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})

    class Base:
        pass

    system.patch_type(Base)

    calls = 0

    class Sub(Base):
        def extra(self):
            nonlocal calls
            calls += 1
            return f"value-{calls}"

    writer = MemoryWriter()

    with record_context(system, writer):
        obj = Sub()
        recorded = obj.extra()

    assert recorded == "value-1"
    assert len(writer.tape) == 1
    assert stream._is_bind_open(writer.tape[0])
    assert stream._bind_index(writer.tape[0]) == 0

    calls = 100
    with replay_context(system, writer.reader()):
        obj = Sub()
        replayed = obj.extra()

    assert replayed == "value-101"
    assert calls == 101, "subclass-only method should execute normally on replay"


def test_system_preexisting_instance_stays_live_in_record_and_replay():
    """Instances created before patch_type() remain unbound on both paths."""
    system = System()

    class Database:
        def __init__(self):
            self.calls = 0

        def query(self):
            self.calls += 1
            return self.calls

    db = Database()
    system.patch_type(Database)

    writer = MemoryWriter()

    assert not system.is_bound(db)

    with record_context(system, writer):
        assert db.query() == 1

    assert writer.tape == []

    with replay_context(system, writer.reader()):
        assert db.query() == 2


def test_system_location_property():
    system = System()
    writer = MemoryWriter()

    assert system.location == "disabled"

    with record_context(system, writer):
        assert system.location == "internal"
        # ext_executor runs with external gate cleared while the call body runs.
        apply_external = system._external.apply_with(None)
        assert apply_external(lambda: system.location) == "external"

    assert system.location == "disabled"


def test_system_context_constructs_passthrough_for_immutable_and_bound(monkeypatch):
    passthroughs = []

    def spy_adapter(*, passthrough, **kwargs):
        passthroughs.append(passthrough)
        return utils.noop

    system = System()
    system.immutable_types.update({int})

    class Base:
        pass

    system.patch_type(Base)

    spec = SimpleNamespace(
        proxy = utils.noop,
        on_call = None,
        on_result = None,
        on_error = None,
    )

    monkeypatch.setattr(context_mod, "adapter", spy_adapter)

    create_context(system, spec, spec)

    assert len(passthroughs) == 2
    assert passthroughs[0] is passthroughs[1] is system.passthrough

    class IntSubclass(int):
        pass

    passthrough = passthroughs[0]

    assert passthrough(1)
    assert passthrough(IntSubclass(1))
    assert passthrough(Base)
    assert not passthrough(str)


def _run_time_server(port: int, ready: threading.Event) -> None:
    """Listen on port, accept one connection, send current time as string, close."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(1)
        ready.set()
        conn, _ = s.accept()
        with conn:
            conn.sendall(str(time.time()).encode())
        conn.close()


def test_system_record_replay_socket_time():
    """patch_type socket, record a connection, replay returns stored result."""
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    src_path = str(repo_root / "src")
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{existing}" if existing else src_path

    code = textwrap.dedent(
        """
        import _socket
        import socket
        import threading
        import time

        from retracesoftware.testing.memorytape import MemoryWriter
        from retracesoftware.proxy.contexts import record_context, replay_context
        from retracesoftware.proxy.system import System


        def run_time_server(port, ready):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("127.0.0.1", port))
                s.listen(1)
                ready.set()
                conn, _ = s.accept()
                with conn:
                    conn.sendall(str(time.time()).encode())
                conn.close()


        system = System()
        system.immutable_types.update({float, int, str, bytes, bool, type, type(None)})
        system.patch_type(_socket.socket)
        writer = MemoryWriter()

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tmp:
            tmp.bind(("127.0.0.1", 0))
            port = tmp.getsockname()[1]

        ready = threading.Event()
        server_thread = threading.Thread(target=lambda: run_time_server(port, ready))
        server_thread.start()
        ready.wait(timeout=2.0)

        with record_context(system, writer):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            data = s.recv(64)
            s.close()
            recorded_time = float(data.decode())

        server_thread.join(timeout=2.0)

        with replay_context(system, writer.reader()):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            data = s.recv(64)
            s.close()
            replayed_time = float(data.decode())

        assert replayed_time == recorded_time
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_system_ext_to_int_callback():
    """list.sort() (C) triggers __lt__ (internal callback) during record/replay.

    Comparable is patched — its methods become external.
    Tracked(Comparable) subclass __lt__ becomes internal via init_subclass.
    When list.sort() calls __lt__, the internal gate routes the callback.
    """
    lt_calls: list = []

    class Comparable:
        """Base type to be patched — methods become external."""
        def __init__(self, val):
            self.val = val

        def __lt__(self, other):
            return self.val < other.val

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(Comparable)

    # Tracked is defined AFTER patch_type so __init_subclass__ patches __lt__ as internal
    class Tracked(Comparable):
        """Subclass — __lt__ goes through the internal gate."""
        def __lt__(self, other):
            lt_calls.append((self.val, other.val))
            return self.val < other.val

    writer = MemoryWriter()
    items = [Tracked(3), Tracked(1), Tracked(2)]

    # Record — list.sort() is C code, __lt__ fires through the internal gate
    with record_context(system, writer):
        items.sort()

    assert [t.val for t in items] == [1, 2, 3]
    assert len(lt_calls) > 0, "__lt__ callback should have fired during sort"
    lt_calls.clear()

    # Replay — same sort, __lt__ callbacks should fire again
    items = [Tracked(3), Tracked(1), Tracked(2)]
    with replay_context(system, writer.reader()):
        items.sort()

    assert [t.val for t in items] == [1, 2, 3]
    assert len(lt_calls) > 0, "__lt__ callback should have fired during replay"


def test_system_external_method_replays_async_callback_automatically():
    """Replaying an external method should automatically drive async callbacks.

    ``Base.run`` is an external method on a patched type. It calls
    ``self.callback(...)`` while the external gate is temporarily cleared, so
    the subclass override crosses the ext->int boundary and records an
    ``ASYNC_CALL``. On replay, calling ``run`` again should re-trigger the live
    Python override automatically; replay should not require the test to call
    ``callback`` manually.
    """

    callback_calls = []

    class Base:
        def callback(self, value):
            raise NotImplementedError

        def run(self, value):
            return self.callback(value) + 1

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(Base)

    class Child(Base):
        def callback(self, value):
            callback_calls.append(value)
            return value * 2

    writer = MemoryWriter()

    with record_context(system, writer):
        recorded = Child().run(5)

    assert recorded == 11
    assert callback_calls == [5]
    assert "ASYNC_CALL" in writer.tape

    callback_calls.clear()

    with replay_context(system, writer.reader()):
        replayed = Child().run(5)

    assert replayed == 11
    assert callback_calls == [5]


def test_system_records_dynamic_int_proxy_callback_identity_as_binding_lookup():
    """Dynamic int-proxy method callbacks record wrapped identity, not raw target.

    An internal object crosses out to an external method, which then calls one
    of its named methods. The callback fn written in ASYNC_CALL should be the
    bound wrapped method identity (`stream.Binding`), not the raw underlying
    function object.
    """

    callback_calls = []

    class External:
        def run(self, callback_obj):
            return callback_obj.readable() + 1

    class Callback:
        def readable(self):
            callback_calls.append("called")
            return 10

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(External)

    writer = MemoryWriter()

    with record_context(system, writer):
        recorded = External().run(Callback())

    assert recorded == 11
    assert callback_calls == ["called"]

    idx = writer.tape.index("ASYNC_CALL")
    assert "CHECKPOINT" not in writer.tape
    assert isinstance(writer.tape[idx + 1], stream.Binding)

    callback_calls.clear()

    with replay_context(system, writer.reader()):
        replayed = External().run(Callback())

    assert replayed == 11
    assert callback_calls == ["called"]


def test_system_records_dynamic_int_proxy_callback_receiver_as_binding_lookup():
    """Dynamic int-proxy callback args should record by binding lookup too.

    Recording the live proxy receiver object into the in-memory tape reuses the
    record-phase object during replay, which is especially toxic for stateful
    objects like ``socket.SocketIO`` that are closed at the end of record.
    """

    class External:
        def run(self, callback_obj):
            return callback_obj.readable() + 1

    class Callback:
        def __init__(self):
            self.closed = False

        def readable(self):
            if self.closed:
                raise ValueError("closed")
            return 10

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(External)

    writer = MemoryWriter()

    callback = Callback()
    with record_context(system, writer):
        recorded = External().run(callback)
    callback.closed = True

    assert recorded == 11

    idx = writer.tape.index("ASYNC_CALL")
    assert isinstance(writer.tape[idx + 1], stream.Binding)
    assert isinstance(writer.tape[idx + 2], tuple)
    assert isinstance(writer.tape[idx + 2][0], stream.Binding)


def test_system_init_subclass_only_wraps_overrides():
    """Subclass-only methods stay plain Python; overrides become internal."""

    class Base:
        def compute(self):
            return 42

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(Base)

    class Sub(Base):
        def compute(self):
            return 100

        def helper(self):
            return 200

    assert isinstance(Sub.compute, utils.wrapped_function)
    assert not isinstance(Sub.helper, utils.wrapped_function)


def test_system_replay_runs_python_subclass_init_for_patched_base():
    """Replay should still execute a Python subclass ``__init__`` body.

    This is the minimal constructor replay regression behind the Flask
    ``socket.socket`` issue. ``Base`` is the patched type family and ``Child``
    is a Python subclass created after patching.

    ``Base.__init__`` is still a patched/base method, so it records/replays as a
    normal external call returning ``None``. ``Child.__init__`` is different: it
    is Python-level setup code and should run directly when called from inside
    the sandbox, even during replay.

    The tape shape here is also intentional: constructor replay should not
    depend on an async callback for ``Child.__init__``. We only expect the
    normal allocation bind plus the recorded ``None`` result from
    ``Base.__init__``.
    """

    class Base:
        def __init__(self):
            return None

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(Base)

    class Child(Base):
        def __init__(self):
            super().__init__()
            self.child_ran = True

    writer = MemoryWriter()

    with record_context(system, writer):
        recorded = Child()

    assert recorded.child_ran is True
    assert len(writer.tape) == 4
    assert stream._is_bind_open(writer.tape[0])
    assert stream._bind_index(writer.tape[0]) == 0
    assert writer.tape[1:] == ["CALL", "RESULT", None]

    with replay_context(system, writer.reader()):
        replayed = Child()

    assert replayed.child_ran is True


def test_patch_type_wraps_c_data_descriptors():
    system = System()

    class Slotted:
        __slots__ = ("x",)

        def read(self):
            return 1

    class WithDict:
        def read(self):
            return 2

    system.patch_type(Slotted)
    system.patch_type(WithDict)

    assert isinstance(Slotted.__dict__["x"], utils._WrappedBase)
    assert not isinstance(WithDict.__dict__["__dict__"], utils._WrappedBase)
    assert isinstance(Slotted.__dict__["read"], utils.wrapped_function)
    assert isinstance(WithDict.__dict__["read"], utils.wrapped_function)

    obj = Slotted()
    obj.x = 7
    assert obj.x == 7


def test_system_direct_override_call_stays_live_inside_sandbox():
    """Direct in-sandbox calls to subclass overrides should stay live."""

    recorded_calls = []
    recorded_results = []

    class Base:
        def compute(self):
            return 42

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(Base)

    class Sub(Base):
        def compute(self):
            return 100

    class TrackingWriter(MemoryWriter):
        def async_call(self, *a, **kw):
            recorded_calls.append((a, kw))
            super().async_call(*a, **kw)

        def write_result(self, value):
            recorded_results.append(value)
            super().write_result(value)

    with record_context(system, TrackingWriter()):
        result = Sub().compute()

    assert result == 100
    assert recorded_calls == []
    assert recorded_results == []


def test_system_ext_int_ext_callback():
    """ext→int→ext: patched method → Python override → outbound external call.

    Base.process (external) internally calls self.compute().
    Sub.compute (internal override) calls self.fetch() (external).

    The full chain:  process [ext] → compute [int] → fetch [ext]

    For correct record/replay, the int_executor must restore the
    external gate when handling the compute() callback, so that
    fetch() inside compute() goes through the external pipeline and
    gets recorded.

    With the passthrough bug (int_executor sees external=None inside
    an ext call → functional.apply), compute() runs with the external
    gate cleared, so fetch() is called directly and never recorded.
    """
    recorded_results = []
    recorded_calls = []

    class Base:
        def process(self):
            return self.compute()

        def compute(self):
            return 42

        def fetch(self):
            return 100

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(Base)

    # Sub defined AFTER patch_type → __init_subclass__ wraps compute as internal
    class Sub(Base):
        def compute(self):
            val = self.fetch()   # outbound ext call from within the callback
            return val * 2

    # Use a custom writer here to track calls separately from results
    class TrackingWriter(MemoryWriter):
        def async_call(self, *a, **kw):
            recorded_calls.append(('call', a, kw))

        def write_result(self, *a, **kw):
            recorded_results.append(a[0] if a else kw.get('result'))
            super().write_result(*a, **kw)

    with record_context(system, TrackingWriter()):
        obj = Sub()
        result = obj.process()

    # process() → compute() → fetch() returns 100 → compute returns 200
    assert result == 200

    # Fixed behavior: compute callback is recorded and nested fetch() is
    # executed under a restored external gate, so fetch result is recorded too.
    assert len(recorded_calls) > 0, (
        f"compute callback should be recorded, got {recorded_calls}")
    assert 100 in recorded_results, (
        f"fetch() result should be recorded, got {recorded_results}")
    assert 200 in recorded_results, (
        f"process() result should be recorded, got {recorded_results}")


def test_system_ext_int_ext_callback_should_record_nested_external():
    """Expected behavior: ext→int callback restores ext gate for nested ext calls.

    This is a RED test that captures the desired gate semantics:
      process [ext] -> compute [int callback] -> fetch [nested ext]

    During record, both callback entry and nested ext result should be recorded.
    Today this fails because int_executor takes passthrough when external is None.
    """
    recorded_results = []
    recorded_calls = []

    class Base:
        def process(self):
            return self.compute()

        def compute(self):
            return 42

        def fetch(self):
            return 100

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(Base)

    class Sub(Base):
        def compute(self):
            # Nested outbound external call from inside callback.
            return self.fetch() * 2

    class TrackingWriter(MemoryWriter):
        def async_call(self, *a, **kw):
            recorded_calls.append((a, kw))

        def write_result(self, *a, **kw):
            recorded_results.append(a[0] if a else kw.get("result"))
            super().write_result(*a, **kw)

    with record_context(system, TrackingWriter()):
        obj = Sub()
        result = obj.process()

    assert result == 200
    # Expected (future fixed behavior): callback + nested ext are both observed.
    assert len(recorded_calls) > 0, "compute() callback should be recorded"
    assert 100 in recorded_results, "nested fetch() result should be recorded"
    assert 200 in recorded_results, "process() result should be recorded"


def test_system_normalize_checkpoint():
    """normalize checkpoints external results and detects divergence on replay.

    During record, normalize(result) is written via writer.checkpoint.
    During replay, normalize(result) is compared via reader.checkpoint.
    If internal code produces a different value, the checkpoint fails.
    """

    class Base:
        def __init__(self, val):
            self.val = val

        def fetch(self):
            return self.val

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(Base)

    normalize = lambda value: (type(value).__name__, value)

    delta = (0, ((("/tmp/normalize_checkpoint.py", 10),),))

    class ConstantStackFactory:
        def delta(self):
            return delta

    writer = MemoryWriter(stackfactory=ConstantStackFactory())
    writer._checkpoint_stackfactory = ConstantStackFactory()

    # Record with normalize — checkpoints are stored
    with record_context(system, writer, normalize=normalize):
        obj = Base(42)
        val = obj.fetch()

    assert val == 42
    assert 'RESULT' in writer.tape, "fetch result should be recorded"
    assert 'CHECKPOINT' in writer.tape, "normalize should have produced checkpoints"

    # Replay with normalize — checkpoints are compared
    with replay_context(
        system,
        writer.reader(stacktrace_factory=ConstantStackFactory()),
        normalize=normalize,
    ):
        obj = Base(42)
        val = obj.fetch()

    assert val == 42


def test_system_normalize_skips_direct_call_on_live_override_instance():
    """Direct calls on preexisting override instances stay live and uncheckpointed."""

    class Base:
        def compute(self):
            return 0

    system = System()
    system.immutable_types.update({int, str, bytes, bool, type, type(None), float})
    system.patch_type(Base)

    # Sub defined after patch_type — compute becomes internal
    diverge = False

    class Sub(Base):
        def compute(self):
            return 999 if diverge else 200

    normalize = lambda value: (type(value).__name__, value)

    writer = MemoryWriter()
    obj = Sub()

    with record_context(system, writer, normalize=normalize):
        result = obj.compute()
    assert result == 200
    assert writer.tape == []

    diverge = True
    with replay_context(system, writer.reader(), normalize=normalize):
        assert obj.compute() == 999


def test_system_proxied_return_value():
    """A patched method returns a list — not immutable, so it gets proxied.

    This exercises the proxy factory path: should_proxy(list) → True,
    so the return value is wrapped in a DynamicProxy whose methods
    read from the stream during replay.

    The list is deliberately NOT in immutable_types.  During record the
    adapter's proxy_output wraps it.  During replay the reader returns
    the wrapped object and proxy_output unwraps it (Wrapped → unwrap),
    giving back the original list contents.

    Uses a subclass (Repo) allocated inside each context so the object
    is bound before external calls are routed through retrace.
    """

    class Database:
        """Base type to be patched — methods become external."""
        def query(self):
            return [1, 2, 3]

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Database)

    class Repo(Database):
        """Subclass — inherits query() as external."""
        pass

    writer = MemoryWriter()

    with record_context(system, writer):
        db = Repo()
        result = db.query()

    assert result == [1, 2, 3]
    assert len(writer.tape) > 0, "query() result should be recorded"

    with replay_context(system, writer.reader()):
        db = Repo()
        result2 = db.query()

    assert result2 == [1, 2, 3], f"replay should return [1, 2, 3], got {result2}"


# ---------------------------------------------------------------------------
# Error replay
# ---------------------------------------------------------------------------

def test_system_error_replay():
    """A patched method raises during record — replay re-raises the same exception.

    The adapter records the exception via write_error (exc_type, exc_value,
    exc_tb).  During replay, read_result raises the stored exception.
    """

    class Service:
        def fail(self):
            raise ValueError("service down")

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Service)

    class MyService(Service):
        pass

    writer = MemoryWriter()

    with record_context(system, writer):
        svc = MyService()
        try:
            svc.fail()
            assert False, "should have raised"
        except ValueError as e:
            recorded_msg = str(e)

    assert recorded_msg == "service down"
    assert 'ERROR' in writer.tape, "exception should be recorded"

    with replay_context(system, writer.reader()):
        svc = MyService()
        try:
            svc.fail()
            assert False, "should have raised on replay"
        except ValueError as e:
            replayed_msg = str(e)

    assert replayed_msg == recorded_msg


def test_system_error_then_success():
    """An exception followed by a normal return — stream stays in sync.

    First call raises, second call succeeds.  Both are recorded.
    During replay, the first call re-raises and the second returns
    the recorded value.
    """

    call_count = 0

    class Flaky:
        def attempt(self):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("first attempt fails")
            return call_count

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Flaky)

    class MyFlaky(Flaky):
        pass

    writer = MemoryWriter()

    with record_context(system, writer):
        obj = MyFlaky()
        try:
            obj.attempt()
        except ConnectionError:
            pass
        result = obj.attempt()

    assert result == 2

    # Replay — counter does NOT advance, values come from stream
    call_count = 0
    with replay_context(system, writer.reader()):
        obj = MyFlaky()
        try:
            obj.attempt()
        except ConnectionError:
            pass
        replayed = obj.attempt()

    assert replayed == 2
    assert call_count == 0, "real function should not execute during replay"


# ---------------------------------------------------------------------------
# Direct instantiation inside context
# ---------------------------------------------------------------------------

def test_system_direct_instantiation_inside_context():
    """Creating an instance of the directly-patched type inside a context.

    This was previously broken (set_on_alloc → Gate.__call__ bug).
    Now _on_alloc is a plain cond callable, so this should work.
    """

    class Sensor:
        def __init__(self, reading):
            self.reading = reading

        def read(self):
            return self.reading

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Sensor)

    writer = MemoryWriter()

    with record_context(system, writer):
        s = Sensor(42)
        val = s.read()

    assert val == 42

    with replay_context(system, writer.reader()):
        s = Sensor(42)
        val2 = s.read()

    assert val2 == 42


# ---------------------------------------------------------------------------
# Multiple patched functions interleaved
# ---------------------------------------------------------------------------

def test_system_multiple_patch_functions():
    """Two standalone functions patched via patch_function, interleaved calls.

    Verifies the stream records and replays calls to different functions
    in the correct order.
    """
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patched_time = system.patch_function(time.time)
    patched_mono = system.patch_function(time.monotonic)

    writer = MemoryWriter()

    with record_context(system, writer):
        t1 = patched_time()
        m1 = patched_mono()
        t2 = patched_time()
        m2 = patched_mono()

    assert t1 <= t2
    assert m1 <= m2

    with replay_context(system, writer.reader()):
        t1_r = patched_time()
        m1_r = patched_mono()
        t2_r = patched_time()
        m2_r = patched_mono()

    assert t1_r == t1
    assert m1_r == m1
    assert t2_r == t2
    assert m2_r == m2


# ---------------------------------------------------------------------------
# Deep proxy chain
# ---------------------------------------------------------------------------

def test_system_deep_proxy_chain():
    """Non-immutable returns non-immutable returns non-immutable.

    Outer.make() → Middle, Middle.descend() → Inner, Inner.value() → int.
    list is NOT in immutable_types so Middle and Inner get proxied.
    The full chain of method calls replays correctly.
    """

    class Outer:
        def make(self):
            return Middle(10)

    class Middle:
        def __init__(self, val):
            self.val = val
        def descend(self):
            return Inner(self.val + 1)

    class Inner:
        def __init__(self, val):
            self.val = val
        def value(self):
            return self.val

    system = System()
    # Middle and Inner are NOT patched and NOT immutable → proxied
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Outer)

    class MyOuter(Outer):
        pass

    writer = MemoryWriter()
    obj = MyOuter()

    with record_context(system, writer):
        mid = obj.make()
        inner = mid.descend()
        val = inner.value()

    assert val == 11

    obj = MyOuter()
    with replay_context(system, writer.reader()):
        mid_r = obj.make()
        inner_r = mid_r.descend()
        val_r = inner_r.value()

    assert val_r == 11


# ---------------------------------------------------------------------------
# Multiple method calls on same proxied return value
# ---------------------------------------------------------------------------

def test_system_proxied_multiple_methods():
    """Call several methods on the same proxied return value.

    Verifies the stream replays in order per-object when multiple
    methods are called on a single non-immutable result.
    """

    class Box:
        def __init__(self, val):
            self.val = val
        def get(self):
            return self.val
        def label(self):
            return f"box-{self.val}"
        def double(self):
            return self.val * 2

    class Factory:
        def create(self, val):
            return Box(val)

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Factory)

    class MyFactory(Factory):
        pass

    writer = MemoryWriter()
    f = MyFactory()

    with record_context(system, writer):
        box = f.create(7)
        v1 = box.get()
        v2 = box.get()
        lbl = box.label()
        dbl = box.double()

    assert v1 == 7
    assert v2 == 7
    assert lbl == "box-7"
    assert dbl == 14

    f = MyFactory()
    with replay_context(system, writer.reader()):
        box_r = f.create(7)
        v1_r = box_r.get()
        v2_r = box_r.get()
        lbl_r = box_r.label()
        dbl_r = box_r.double()

    assert v1_r == 7
    assert v2_r == 7
    assert lbl_r == "box-7"
    assert dbl_r == 14


# ---------------------------------------------------------------------------
# Immutable values pass through without proxying
# ---------------------------------------------------------------------------

def test_system_immutable_passthrough():
    """Values of immutable types pass through without being wrapped.

    After replay, the returned value should be the exact Python type
    (int, str, etc.), not a DynamicProxy wrapper.
    """
    import retracesoftware.utils as utils

    class Calculator:
        def add(self, a, b):
            return a + b

        def name(self):
            return "calc"

        def data(self):
            return b"raw"

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Calculator)

    class MyCalc(Calculator):
        pass

    writer = MemoryWriter()
    c = MyCalc()

    with record_context(system, writer):
        i = c.add(3, 4)
        s = c.name()
        b = c.data()

    assert i == 7 and type(i) is int
    assert s == "calc" and type(s) is str
    assert b == b"raw" and type(b) is bytes

    c = MyCalc()
    with replay_context(system, writer.reader()):
        i_r = c.add(3, 4)
        s_r = c.name()
        b_r = c.data()

    assert i_r == 7 and type(i_r) is int, f"expected int, got {type(i_r)}"
    assert s_r == "calc" and type(s_r) is str, f"expected str, got {type(s_r)}"
    assert b_r == b"raw" and type(b_r) is bytes, f"expected bytes, got {type(b_r)}"


# ---------------------------------------------------------------------------
# disable_for
# ---------------------------------------------------------------------------

def test_system_disable_for():
    """disable_for clears both gates — patched methods pass through.

    Inside a record_context, a function wrapped with disable_for
    should call the original directly (not through the adapter).
    """
    call_log = []

    class Logger:
        def log(self, msg):
            call_log.append(msg)
            return msg

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Logger)

    class MyLogger(Logger):
        pass

    disabled_log = system.disable_for(lambda msg: MyLogger().log(msg))

    writer = MemoryWriter()

    with record_context(system, writer):
        obj = MyLogger()
        # Normal patched call — goes through adapter, recorded
        r1 = obj.log("recorded")

        # Disabled call — gates cleared, NOT recorded
        r2 = disabled_log("not recorded")

        # Another normal call
        r3 = obj.log("also recorded")

    assert r1 == "recorded"
    assert r2 == "not recorded"
    assert r3 == "also recorded"

    # The tape should contain results for r1 and r3, but not r2.
    # Count RESULT tags — should be 2 (not 3)
    result_count = sum(1 for x in writer.tape if x == 'RESULT')
    assert result_count == 2, f"expected 2 results (r1, r3), got {result_count}"


# ---------------------------------------------------------------------------
# patch_function at system level
# ---------------------------------------------------------------------------

def test_system_patch_function():
    """system.patch_function wraps a standalone function through the external gate.

    Outside a context, calls pass through directly.
    Inside record_context, calls are recorded.
    Inside replay_context, the recorded value is returned without executing.
    """
    counter = 0

    def get_count():
        nonlocal counter
        counter += 1
        return counter

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patched = system.patch_function(get_count)

    # Outside context — direct passthrough
    assert patched() == 1
    assert patched() == 2

    writer = MemoryWriter()

    with record_context(system, writer):
        recorded = patched()

    assert recorded == 3
    assert 'RESULT' in writer.tape

    # Replay — counter should NOT advance
    old_counter = counter
    with replay_context(system, writer.reader()):
        replayed = patched()

    assert replayed == 3, f"replay should return 3, got {replayed}"
    assert counter == old_counter, "counter should not advance during replay"


# ---------------------------------------------------------------------------
# None return
# ---------------------------------------------------------------------------

def test_system_none_return():
    """A patched method that returns None — the immutable None passes through."""

    class Void:
        def noop(self):
            return None

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Void)

    class MyVoid(Void):
        pass

    writer = MemoryWriter()
    obj = MyVoid()

    with record_context(system, writer):
        r1 = obj.noop()
        r2 = obj.noop()

    assert r1 is None
    assert r2 is None

    obj = MyVoid()
    with replay_context(system, writer.reader()):
        r1_r = obj.noop()
        r2_r = obj.noop()

    assert r1_r is None
    assert r2_r is None


# ---------------------------------------------------------------------------
# Interleaved patched types
# ---------------------------------------------------------------------------

def test_system_interleaved_patched_types():
    """Two different types patched in the same system, interleaved calls.

    Verifies the stream records and replays calls from both types
    in the correct order without cross-contamination.
    """

    class Clock:
        def __init__(self):
            self.ticks = 0
        def tick(self):
            self.ticks += 1
            return self.ticks

    class Counter:
        def __init__(self):
            self.n = 0
        def inc(self):
            self.n += 1
            return self.n * 10

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Clock)
    system.patch_type(Counter)

    class MyClock(Clock):
        pass

    class MyCounter(Counter):
        pass

    writer = MemoryWriter()
    c = MyClock()
    n = MyCounter()

    with record_context(system, writer):
        t1 = c.tick()
        n1 = n.inc()
        t2 = c.tick()
        n2 = n.inc()

    assert (t1, n1, t2, n2) == (1, 10, 2, 20)

    c = MyClock()
    n = MyCounter()
    with replay_context(system, writer.reader()):
        t1_r = c.tick()
        n1_r = n.inc()
        t2_r = c.tick()
        n2_r = n.inc()

    assert (t1_r, n1_r, t2_r, n2_r) == (1, 10, 2, 20)


# ── Stack trace tests ─────────────────────────────────────────

def test_system_record_with_stacktraces():
    """record_context(stacktraces=True) writes StacktraceMessage entries."""
    system = System()
    system.immutable_types.update({int, str, float, bytes, bool, type(None)})

    sf = utils.StackFactory()
    writer = MemoryWriter(stackfactory=sf)

    class Clock:
        def tick(self): return 42

    system.patch_type(Clock)

    with record_context(system, writer, stacktraces=True):
        c = Clock()
        result = c.tick()

    assert result == 42

    stack_messages = [m for m in writer.tape if isinstance(m, StacktraceMessage)]
    assert len(stack_messages) > 0, "Expected at least one StacktraceMessage"

    for msg in stack_messages:
        assert isinstance(msg.stacktrace, tuple)


def test_system_bind_writes_stacktrace_before_binding_when_enabled():
    """Object binds emit a preceding StacktraceMessage when enabled."""
    system = System()

    sf = utils.StackFactory()
    writer = MemoryWriter(stackfactory=sf)

    class Patched:
        pass

    system.patch_type(Patched)

    with record_context(system, writer, stacktraces=True):
        obj = Patched()

    assert isinstance(writer.tape[0], StacktraceMessage)
    assert stream._is_bind_open(writer.tape[1])


def test_system_enabled_returns_boolean_false_when_inactive():
    system = System()

    assert system.enabled() is False


def test_wrap_start_new_thread_passthroughs_when_system_disabled():
    system = System()
    seen = {}

    def original_start_new_thread(function, args, kwargs=None):
        seen["function"] = function
        seen["args"] = args
        seen["kwargs"] = kwargs
        return 123

    wrapped_start_new_thread = system.wrap_start_new_thread(original_start_new_thread)

    def target():
        return "ok"

    assert wrapped_start_new_thread(target, ()) == 123
    assert seen == {
        "function": target,
        "args": (),
        "kwargs": None,
    }


def test_system_stacktraces_disabled_by_default():
    """record_context without stacktraces=True writes no STACKTRACE messages."""
    system = System()
    system.immutable_types.update({int, str, float, bytes, bool, type(None)})

    sf = utils.StackFactory()
    writer = MemoryWriter(stackfactory=sf)

    class Clock:
        def tick(self): return 42

    system.patch_type(Clock)

    with record_context(system, writer):
        c = Clock()
        result = c.tick()

    assert result == 42

    stack_messages = [m for m in writer.tape if isinstance(m, StacktraceMessage)]
    assert len(stack_messages) == 0


def test_system_stacktraces_replay_skips_messages():
    """Replay correctly skips StacktraceMessage entries on the tape."""
    system = System()
    system.immutable_types.update({int, str, float, bytes, bool, type(None)})

    delta = (0, ((("/tmp/replay_stack.py", 10),),))

    class ConstantStackFactory:
        def delta(self):
            return delta

    writer = MemoryWriter(stackfactory=ConstantStackFactory())

    class Clock:
        def tick(self): return 42
        def tock(self): return 99

    system.patch_type(Clock)

    with record_context(system, writer, stacktraces=True):
        c = Clock()
        r1 = c.tick()
        r2 = c.tock()

    assert r1 == 42
    assert r2 == 99

    stack_messages = [m for m in writer.tape if isinstance(m, StacktraceMessage)]
    assert len(stack_messages) > 0

    # Replay should work correctly despite the HandleMessage entries
    with replay_context(system, writer.reader(stacktrace_factory=ConstantStackFactory())):
        c2 = Clock()
        r1_replay = c2.tick()
        r2_replay = c2.tock()

    assert r1_replay == 42
    assert r2_replay == 99


def test_system_stacktraces_exclude():
    """StackFactory exclude predicate filters functions from the stack trace."""
    system = System()
    system.immutable_types.update({int, str, float, bytes, bool, type(None)})

    sf = utils.StackFactory(exclude=lambda func: func is test_system_stacktraces_exclude)
    writer = MemoryWriter(stackfactory=sf)

    class Clock:
        def tick(self): return 42

    system.patch_type(Clock)

    with record_context(system, writer, stacktraces=True):
        c = Clock()
        result = c.tick()

    assert result == 42

    # Check that no stack frame references the excluded function
    stack_messages = [m for m in writer.tape if isinstance(m, StacktraceMessage)]
    assert len(stack_messages) > 0

    for msg in stack_messages:
        for filename, _lineno in msg.stacktrace:
            assert "test_system_stacktraces_exclude" not in filename


# ---------------------------------------------------------------------------
# Factory function proxy_output involution bug
# ---------------------------------------------------------------------------

def test_factory_function_replay_preserves_proxy():
    """patch_function on a factory — return value methods must replay from stream.

    Bug: proxy_output is an involution (maybe_proxy wraps raw values,
    unwraps Wrapped values).  During recording, the factory's raw return
    is wrapped in a DynamicProxy and stored on the tape.  During replay,
    the replay executor returns the stored DynamicProxy, then proxy_output
    runs AGAIN — sees Wrapped → unwraps it.  The caller receives a raw
    object whose methods bypass the gates entirely.

    This test catches the bug by using a factory whose return type has
    methods with side effects (a counter).  During replay, if the methods
    execute real code (instead of reading from the stream), the counter
    advances and the assertion fails.
    """
    call_count = 0

    class Connection:
        """Non-immutable return type — NOT in patched_types."""
        def execute(self, sql):
            nonlocal call_count
            call_count += 1
            return f"result-{call_count}"

        def close(self):
            nonlocal call_count
            call_count += 1
            return None

    def connect(dsn):
        """Factory function — returns a Connection (non-immutable, non-patched)."""
        return Connection()

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patched_connect = system.patch_function(connect)

    writer = MemoryWriter()

    # ── Record ──
    call_count = 0
    with record_context(system, writer):
        conn = patched_connect("db://test")
        r1 = conn.execute("SELECT 1")
        r2 = conn.execute("SELECT 2")
        conn.close()

    assert r1 == "result-1"
    assert r2 == "result-2"
    recorded_count = call_count  # should be 3 (2 executes + 1 close)

    # ── Replay ──
    call_count = 0
    with replay_context(system, writer.reader()):
        conn_r = patched_connect("db://test")
        r1_r = conn_r.execute("SELECT 1")
        r2_r = conn_r.execute("SELECT 2")
        conn_r.close()

    # Values must match recording
    assert r1_r == "result-1", f"expected 'result-1', got {r1_r!r}"
    assert r2_r == "result-2", f"expected 'result-2', got {r2_r!r}"

    # The real methods must NOT have executed during replay.
    # If they did, call_count > 0 — proving the DynamicProxy was
    # unwrapped and methods bypassed the gates.
    assert call_count == 0, (
        f"factory return value methods executed real code during replay "
        f"(call_count={call_count}); proxy_output unwrapped the DynamicProxy"
    )


# ---------------------------------------------------------------------------
# Callback raises — ported from old test_proxy.py
# ---------------------------------------------------------------------------

def test_system_callback_raises():
    """An ext→int callback that raises is handled correctly during record/replay.

    The external API catches the callback error.  Both the callback exception
    and the final return value must round-trip through the stream.
    """
    error_log = []

    class Base:
        def api(self):
            try:
                result = self.callback()
            except ValueError:
                result = -1
            return result + 100

        def callback(self):
            return 0

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(Base)

    class Sub(Base):
        def callback(self):
            error_log.append("raised")
            raise ValueError("callback error")

    writer = MemoryWriter()

    with record_context(system, writer):
        obj = Sub()
        result = obj.api()

    assert result == 99  # -1 + 100

    error_log.clear()
    with replay_context(system, writer.reader()):
        obj = Sub()
        replayed = obj.api()

    assert replayed == 99


# ---------------------------------------------------------------------------
# Different result types from same method — ported from old test_proxy.py
# ---------------------------------------------------------------------------

def test_system_different_result_types():
    """A method that returns different non-immutable types across calls.

    The proxy factory must handle each return type independently.
    """

    class Dog:
        def __init__(self, name):
            self.name = name
        def speak(self):
            return f"woof from {self.name}"

    class Cat:
        def __init__(self, name):
            self.name = name
        def speak(self):
            return f"meow from {self.name}"

    call_count = 0

    class PetShop:
        def adopt(self, kind):
            nonlocal call_count
            call_count += 1
            if kind == "dog":
                return Dog(f"dog-{call_count}")
            return Cat(f"cat-{call_count}")

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    system.patch_type(PetShop)

    class MyShop(PetShop):
        pass

    writer = MemoryWriter()

    with record_context(system, writer):
        shop = MyShop()
        d = shop.adopt("dog")
        dog_speak = d.speak()
        c = shop.adopt("cat")
        cat_speak = c.speak()

    assert dog_speak == "woof from dog-1"
    assert cat_speak == "meow from cat-2"

    call_count = 0
    with replay_context(system, writer.reader()):
        shop = MyShop()
        d_r = shop.adopt("dog")
        dog_speak_r = d_r.speak()
        c_r = shop.adopt("cat")
        cat_speak_r = c_r.speak()

    assert dog_speak_r == "woof from dog-1"
    assert cat_speak_r == "meow from cat-2"
    assert call_count == 0, "real methods should not execute during replay"


# ---------------------------------------------------------------------------
# Ported from old test_proxy.py — standalone function proxy tests
# ---------------------------------------------------------------------------

def test_system_proxy_time_roundtrip():
    """Proxy time.time via patch_function, record a call, replay returns
    the exact same float."""
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patched = system.patch_function(time.time)
    writer = MemoryWriter()

    with record_context(system, writer):
        recorded = patched()

    assert isinstance(recorded, float)

    with replay_context(system, writer.reader()):
        replayed = patched()

    assert replayed == recorded


def test_system_proxy_multiple_sequential_calls():
    """Three sequential calls to the same patch_function replay in order."""
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patched = system.patch_function(time.time)
    writer = MemoryWriter()

    with record_context(system, writer):
        r1 = patched()
        r2 = patched()
        r3 = patched()

    assert r1 <= r2 <= r3

    with replay_context(system, writer.reader()):
        p1 = patched()
        p2 = patched()
        p3 = patched()

    assert (r1, r2, r3) == (p1, p2, p3)


def test_system_factory_returns_object():
    """patch_function on a factory — methods on the returned non-immutable
    object are recorded and replayed correctly."""

    class Box:
        def __init__(self, val):
            self._val = val
        def get(self):
            return self._val
        def label(self):
            return f"box-{self._val}"

    def make_box(val):
        return Box(val)

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patched = system.patch_function(make_box)
    writer = MemoryWriter()

    with record_context(system, writer):
        box = patched(42)
        val = box.get()
        lbl = box.label()

    assert val == 42
    assert lbl == "box-42"

    with replay_context(system, writer.reader()):
        box_r = patched(42)
        val_r = box_r.get()
        lbl_r = box_r.label()

    assert val_r == 42
    assert lbl_r == "box-42"


def test_system_factory_two_hop_proxy():
    """Factory returns Pair whose methods return Box (two-hop non-immutable chain)."""

    class Box:
        def __init__(self, val):
            self._val = val
        def get(self):
            return self._val

    class Pair:
        def __init__(self, a, b):
            self._a = a
            self._b = b
        def first(self):
            return Box(self._a)
        def second(self):
            return Box(self._b)

    def make_pair(a, b):
        return Pair(a, b)

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patched = system.patch_function(make_pair)
    writer = MemoryWriter()

    with record_context(system, writer):
        pair = patched(10, 20)
        first = pair.first()
        v1 = first.get()
        second = pair.second()
        v2 = second.get()

    assert v1 == 10
    assert v2 == 20

    with replay_context(system, writer.reader()):
        pair_r = patched(10, 20)
        first_r = pair_r.first()
        v1_r = first_r.get()
        second_r = pair_r.second()
        v2_r = second_r.get()

    assert v1_r == 10
    assert v2_r == 20


def test_system_factory_interleaved_objects():
    """Two objects from the same factory, interleaved method calls."""

    class Box:
        def __init__(self, val):
            self._val = val
        def get(self):
            return self._val
        def label(self):
            return f"box-{self._val}"

    def make_box(val):
        return Box(val)

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patched = system.patch_function(make_box)
    writer = MemoryWriter()

    with record_context(system, writer):
        a = patched(100)
        b = patched(200)
        va = a.get()
        vb = b.get()
        la = a.label()
        lb = b.label()

    assert va == 100
    assert vb == 200
    assert la == "box-100"
    assert lb == "box-200"

    with replay_context(system, writer.reader()):
        a_r = patched(100)
        b_r = patched(200)
        va_r = a_r.get()
        vb_r = b_r.get()
        la_r = a_r.label()
        lb_r = b_r.label()

    assert va_r == 100
    assert vb_r == 200
    assert la_r == "box-100"
    assert lb_r == "box-200"


def test_system_mixed_immutable_and_mutable():
    """Interleave immutable returns (time.time→float) with mutable returns
    (factory→Box) in the same session without stream desync."""

    class Box:
        def __init__(self, val):
            self._val = val
        def get(self):
            return self._val
        def label(self):
            return f"box-{self._val}"

    def make_box(val):
        return Box(val)

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    clock = system.patch_function(time.time)
    make = system.patch_function(make_box)
    writer = MemoryWriter()

    with record_context(system, writer):
        t1 = clock()
        box = make(42)
        t2 = clock()
        val = box.get()
        lbl = box.label()

    assert t1 <= t2
    assert val == 42
    assert lbl == "box-42"

    with replay_context(system, writer.reader()):
        t1_r = clock()
        box_r = make(42)
        t2_r = clock()
        val_r = box_r.get()
        lbl_r = box_r.label()

    assert t1_r == t1
    assert t2_r == t2
    assert val_r == 42
    assert lbl_r == "box-42"


def test_system_error_replayed_standalone():
    """A patch_function that always raises — the exception round-trips."""

    def always_raises(msg):
        raise ValueError(msg)

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patched = system.patch_function(always_raises)
    writer = MemoryWriter()

    with record_context(system, writer):
        try:
            patched("boom")
            assert False, "should have raised"
        except ValueError as e:
            recorded_msg = str(e)

    assert recorded_msg == "boom"

    with replay_context(system, writer.reader()):
        try:
            patched("boom")
            assert False, "should have raised on replay"
        except ValueError as e:
            replayed_msg = str(e)

    assert replayed_msg == "boom"


def test_system_error_then_success_standalone():
    """An error followed by a success on standalone functions — stream stays in sync."""

    def always_raises(msg):
        raise ValueError(msg)

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    raises = system.patch_function(always_raises)
    clock = system.patch_function(time.time)
    writer = MemoryWriter()

    with record_context(system, writer):
        try:
            raises("fail")
        except ValueError:
            pass
        t = clock()

    with replay_context(system, writer.reader()):
        try:
            raises("fail")
        except ValueError:
            pass
        t_r = clock()

    assert t == t_r
