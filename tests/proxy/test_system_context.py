"""
Tests for System record_context and replay_context.

Verifies that record_context and replay_context correctly manage
gate lifecycle and route external calls through the pipeline.
"""
import os
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
import retracesoftware.proxy.system as system_mod
from retracesoftware.proxy.system import System
from retracesoftware.proxy.messagestream import MemoryWriter, MemoryReader, HandleMessage

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

    with system.record_context(w):
        assert system._in_sandbox()
        assert system._out_sandbox()

    # After record context: gates restored
    assert not system._in_sandbox()
    assert not system._out_sandbox()

    with system.replay_context(w.reader()):
        assert system._in_sandbox()
        assert system._out_sandbox()

    # After replay context: gates restored
    assert not system._in_sandbox()
    assert not system._out_sandbox()


def test_system_register_thread_id_uses_intern_once():
    system = System()

    class TrackingWriter(MemoryWriter):
        def __init__(self):
            super().__init__()
            self.interned = []
            self.bound = []

        def intern(self, obj):
            self.interned.append(obj)

        def bind(self, obj):
            self.bound.append(obj)

    writer = TrackingWriter()
    thread_id = ()

    with system.record_context(writer):
        assert system.register_thread_id(thread_id) is None
        assert system.register_thread_id(thread_id) is None

    assert writer.interned == [thread_id]
    assert writer.bound == []


def test_system_active_allocations_use_bind_path():
    """External allocations bind, internal allocations report new_patched."""
    system = System()

    class Patched:
        pass

    system.patch_type(Patched)

    bound = []
    new_patched = []

    def bind(obj):
        bound.append(obj)

    def on_new_patched(obj):
        new_patched.append(obj)

    with system._context(_bind=bind, _new_patched=on_new_patched, _external=utils.noop):
        external_obj = Patched()

    assert bound == [external_obj]
    assert new_patched == []
    assert system.is_bound(external_obj)

    bound.clear()
    new_patched.clear()

    with system._context(_bind=bind, _new_patched=on_new_patched, _internal=utils.noop):
        internal_obj = Patched()

    assert bound == []
    assert new_patched == [internal_obj]
    assert system.is_bound(internal_obj)


def test_system_preexisting_instance_stays_unretraced_in_record_and_replay():
    """Instances created before patch_type() remain live on both paths."""
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

    with system.record_context(writer):
        assert db.query() == 1

    assert writer.tape == []

    with system.replay_context(writer.reader()):
        assert db.query() == 2


def test_system_location_property():
    system = System()
    writer = MemoryWriter()

    assert system.location == "disabled"

    with system.record_context(writer):
        assert system.location == "internal"
        # ext_executor runs with external gate cleared while the call body runs.
        apply_external = system._external.apply_with(None)
        assert apply_external(lambda: system.location) == "external"

    assert system.location == "disabled"


def test_system_context_constructs_passthrough_for_immutable_and_patched(monkeypatch):
    system = System()
    system.immutable_types.update({int})

    class Base:
        pass

    system.patch_type(Base)

    predicates = []
    passthroughs = []

    class SpyFastTypePredicate:
        def __init__(self, predicate):
            predicates.append(predicate)

        def __call__(self, obj):
            return False

        def istypeof(self, obj):
            return False

    def spy_adapter(*, passthrough, **kwargs):
        passthroughs.append(passthrough)
        return utils.noop

    monkeypatch.setattr(utils, "FastTypePredicate", SpyFastTypePredicate)
    monkeypatch.setattr(system_mod, "adapter", spy_adapter)

    spec = SimpleNamespace(
        proxy = utils.noop,
        on_call = None,
        on_result = None,
        on_error = None,
    )

    system._create_context(spec, spec)

    assert len(predicates) == 1
    assert len(passthroughs) == 2
    assert passthroughs[0] is passthroughs[1]

    predicate = predicates[0]

    class IntSubclass(int):
        pass

    assert predicate(int)
    assert predicate(IntSubclass)
    assert predicate(Base)
    assert not predicate(str)


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

        from retracesoftware.proxy.messagestream import MemoryWriter
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

        with system.record_context(writer):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect(("127.0.0.1", port))
            data = s.recv(64)
            s.close()
            recorded_time = float(data.decode())

        server_thread.join(timeout=2.0)

        with system.replay_context(writer.reader()):
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
    with system.record_context(writer):
        items.sort()

    assert [t.val for t in items] == [1, 2, 3]
    assert len(lt_calls) > 0, "__lt__ callback should have fired during sort"
    lt_calls.clear()

    # Replay — same sort, __lt__ callbacks should fire again
    items = [Tracked(3), Tracked(1), Tracked(2)]
    with system.replay_context(writer.reader()):
        items.sort()

    assert [t.val for t in items] == [1, 2, 3]
    assert len(lt_calls) > 0, "__lt__ callback should have fired during replay"


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
        def write_call(self, *a, **kw):
            recorded_calls.append(('call', a, kw))

        def write_result(self, *a, **kw):
            recorded_results.append(a[0] if a else kw.get('result'))
            super().write_result(*a, **kw)

    obj = Sub()

    with system.record_context(TrackingWriter()):
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
        def write_call(self, *a, **kw):
            recorded_calls.append((a, kw))

        def write_result(self, *a, **kw):
            recorded_results.append(a[0] if a else kw.get("result"))
            super().write_result(*a, **kw)

    obj = Sub()
    with system.record_context(TrackingWriter()):
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

    writer = MemoryWriter()

    # Record with normalize — checkpoints are stored
    with system.record_context(writer, normalize=normalize):
        obj = Base(42)
        val = obj.fetch()

    assert val == 42
    assert 'RESULT' in writer.tape, "fetch result should be recorded"
    assert 'CHECKPOINT' in writer.tape, "normalize should have produced checkpoints"

    # Replay with normalize — checkpoints are compared
    with system.replay_context(writer.reader(), normalize=normalize):
        obj = Base(42)
        val = obj.fetch()

    assert val == 42


def test_system_normalize_detects_divergence():
    """normalize raises when replay produces a different internal result.

    Sub.compute (internal override) is called directly inside the
    context — not nested in an external call — so the int_executor
    takes the adapter branch and the checkpoint fires.

    During record, compute returns 200 and the checkpoint is stored.
    During replay, we force compute to return 999.  The checkpoint
    catches the mismatch.

    NOTE: if compute were called from within a base class external
    method (e.g. Base.process → self.compute()), the passthrough
    branch would skip the checkpoint entirely.  This test exercises
    the direct-call path where the adapter does fire.
    """

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

    # Record — direct call to internal method, adapter branch fires
    with system.record_context(writer, normalize=normalize):
        result = obj.compute()
    assert result == 200
    assert 'CHECKPOINT' in writer.tape, "compute result should be checkpointed"

    # Replay — compute returns 999, checkpoint catches the mismatch
    diverge = True
    obj = Sub()
    with pytest.raises(AssertionError, match="replay divergence"):
        with system.replay_context(writer.reader(), normalize=normalize):
            obj.compute()


def test_system_proxied_return_value():
    """A patched method returns a list — not immutable, so it gets proxied.

    This exercises the proxy factory path: should_proxy(list) → True,
    so the return value is wrapped in a DynamicProxy whose methods
    read from the stream during replay.

    The list is deliberately NOT in immutable_types.  During record the
    adapter's proxy_output wraps it.  During replay the reader returns
    the wrapped object and proxy_output unwraps it (Wrapped → unwrap),
    giving back the original list contents.

    Uses a subclass (Repo) of the patched base (Database) to avoid
    the pre-existing set_on_alloc issue with direct instantiation of
    the patched type outside a context.
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
    db = Repo()

    with system.record_context(writer):
        result = db.query()

    assert result == [1, 2, 3]
    assert len(writer.tape) > 0, "query() result should be recorded"

    db = Repo()
    with system.replay_context(writer.reader()):
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
    svc = MyService()

    with system.record_context(writer):
        try:
            svc.fail()
            assert False, "should have raised"
        except ValueError as e:
            recorded_msg = str(e)

    assert recorded_msg == "service down"
    assert 'ERROR' in writer.tape, "exception should be recorded"

    svc = MyService()
    with system.replay_context(writer.reader()):
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
    obj = MyFlaky()

    with system.record_context(writer):
        try:
            obj.attempt()
        except ConnectionError:
            pass
        result = obj.attempt()

    assert result == 2

    # Replay — counter does NOT advance, values come from stream
    call_count = 0
    obj = MyFlaky()
    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        s = Sensor(42)
        val = s.read()

    assert val == 42

    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        t1 = patched_time()
        m1 = patched_mono()
        t2 = patched_time()
        m2 = patched_mono()

    assert t1 <= t2
    assert m1 <= m2

    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        mid = obj.make()
        inner = mid.descend()
        val = inner.value()

    assert val == 11

    obj = MyOuter()
    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
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
    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        i = c.add(3, 4)
        s = c.name()
        b = c.data()

    assert i == 7 and type(i) is int
    assert s == "calc" and type(s) is str
    assert b == b"raw" and type(b) is bytes

    c = MyCalc()
    with system.replay_context(writer.reader()):
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
    obj = MyLogger()

    with system.record_context(writer):
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

    with system.record_context(writer):
        recorded = patched()

    assert recorded == 3
    assert 'RESULT' in writer.tape

    # Replay — counter should NOT advance
    old_counter = counter
    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        r1 = obj.noop()
        r2 = obj.noop()

    assert r1 is None
    assert r2 is None

    obj = MyVoid()
    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        t1 = c.tick()
        n1 = n.inc()
        t2 = c.tick()
        n2 = n.inc()

    assert (t1, n1, t2, n2) == (1, 10, 2, 20)

    c = MyClock()
    n = MyCounter()
    with system.replay_context(writer.reader()):
        t1_r = c.tick()
        n1_r = n.inc()
        t2_r = c.tick()
        n2_r = n.inc()

    assert (t1_r, n1_r, t2_r, n2_r) == (1, 10, 2, 20)


# ── Stack trace tests ─────────────────────────────────────────

def test_system_record_with_stacktraces():
    """record_context(stacktraces=True) writes STACKTRACE handle messages."""
    system = System()
    system.immutable_types.update({int, str, float, bytes, bool, type(None)})

    sf = utils.StackFactory()
    writer = MemoryWriter(stackfactory=sf)

    class Clock:
        def tick(self): return 42

    system.patch_type(Clock)

    with system.record_context(writer, stacktraces=True):
        c = Clock()
        result = c.tick()

    assert result == 42

    # Verify STACKTRACE handle messages were written to the tape
    stack_messages = [m for m in writer.tape if isinstance(m, HandleMessage) and m.name == 'STACKTRACE']
    assert len(stack_messages) > 0, "Expected at least one STACKTRACE handle message"

    # Each stack delta should be a (pop_count, frames_to_add) tuple
    for msg in stack_messages:
        pop_count, frames = msg.value
        assert isinstance(pop_count, int)
        assert isinstance(frames, tuple)


def test_system_stacktraces_disabled_by_default():
    """record_context without stacktraces=True writes no STACKTRACE messages."""
    system = System()
    system.immutable_types.update({int, str, float, bytes, bool, type(None)})

    sf = utils.StackFactory()
    writer = MemoryWriter(stackfactory=sf)

    class Clock:
        def tick(self): return 42

    system.patch_type(Clock)

    with system.record_context(writer):
        c = Clock()
        result = c.tick()

    assert result == 42

    stack_messages = [m for m in writer.tape if isinstance(m, HandleMessage)]
    assert len(stack_messages) == 0


def test_system_stacktraces_replay_skips_handles():
    """Replay correctly skips HandleMessage entries on the tape."""
    system = System()
    system.immutable_types.update({int, str, float, bytes, bool, type(None)})

    sf = utils.StackFactory()
    writer = MemoryWriter(stackfactory=sf)

    class Clock:
        def tick(self): return 42
        def tock(self): return 99

    system.patch_type(Clock)

    with system.record_context(writer, stacktraces=True):
        c = Clock()
        r1 = c.tick()
        r2 = c.tock()

    assert r1 == 42
    assert r2 == 99

    # Verify handles are present
    stack_messages = [m for m in writer.tape if isinstance(m, HandleMessage)]
    assert len(stack_messages) > 0

    # Replay should work correctly despite the HandleMessage entries
    with system.replay_context(writer.reader()):
        c2 = Clock()
        r1_replay = c2.tick()
        r2_replay = c2.tock()

    assert r1_replay == 42
    assert r2_replay == 99


def test_system_stacktraces_exclude():
    """StackFactory.exclude filters functions from the stack trace."""
    system = System()
    system.immutable_types.update({int, str, float, bytes, bool, type(None)})

    sf = utils.StackFactory()
    sf.exclude.add(test_system_stacktraces_exclude)
    writer = MemoryWriter(stackfactory=sf)

    class Clock:
        def tick(self): return 42

    system.patch_type(Clock)

    with system.record_context(writer, stacktraces=True):
        c = Clock()
        result = c.tick()

    assert result == 42

    # Check that no stack frame references the excluded function
    stack_messages = [m for m in writer.tape if isinstance(m, HandleMessage) and m.name == 'STACKTRACE']
    assert len(stack_messages) > 0

    for msg in stack_messages:
        _pop_count, frames = msg.value
        for frame in frames:
            assert frame.func is not test_system_stacktraces_exclude


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
    with system.record_context(writer):
        conn = patched_connect("db://test")
        r1 = conn.execute("SELECT 1")
        r2 = conn.execute("SELECT 2")
        conn.close()

    assert r1 == "result-1"
    assert r2 == "result-2"
    recorded_count = call_count  # should be 3 (2 executes + 1 close)

    # ── Replay ──
    call_count = 0
    with system.replay_context(writer.reader()):
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
    obj = Sub()

    with system.record_context(writer):
        result = obj.api()

    assert result == 99  # -1 + 100

    obj = Sub()
    error_log.clear()
    with system.replay_context(writer.reader()):
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
    shop = MyShop()

    with system.record_context(writer):
        d = shop.adopt("dog")
        dog_speak = d.speak()
        c = shop.adopt("cat")
        cat_speak = c.speak()

    assert dog_speak == "woof from dog-1"
    assert cat_speak == "meow from cat-2"

    call_count = 0
    shop = MyShop()
    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        recorded = patched()

    assert isinstance(recorded, float)

    with system.replay_context(writer.reader()):
        replayed = patched()

    assert replayed == recorded


def test_system_proxy_multiple_sequential_calls():
    """Three sequential calls to the same patch_function replay in order."""
    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patched = system.patch_function(time.time)
    writer = MemoryWriter()

    with system.record_context(writer):
        r1 = patched()
        r2 = patched()
        r3 = patched()

    assert r1 <= r2 <= r3

    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        box = patched(42)
        val = box.get()
        lbl = box.label()

    assert val == 42
    assert lbl == "box-42"

    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        pair = patched(10, 20)
        first = pair.first()
        v1 = first.get()
        second = pair.second()
        v2 = second.get()

    assert v1 == 10
    assert v2 == 20

    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
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

    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        t1 = clock()
        box = make(42)
        t2 = clock()
        val = box.get()
        lbl = box.label()

    assert t1 <= t2
    assert val == 42
    assert lbl == "box-42"

    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        try:
            patched("boom")
            assert False, "should have raised"
        except ValueError as e:
            recorded_msg = str(e)

    assert recorded_msg == "boom"

    with system.replay_context(writer.reader()):
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

    with system.record_context(writer):
        try:
            raises("fail")
        except ValueError:
            pass
        t = clock()

    with system.replay_context(writer.reader()):
        try:
            raises("fail")
        except ValueError:
            pass
        t_r = clock()

    assert t == t_r
