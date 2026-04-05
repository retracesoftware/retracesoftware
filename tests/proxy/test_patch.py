"""
Tests for the patch() function from retracesoftware.install.patcher.

Verifies that patch(module, spec, installation) correctly applies TOML-derived
directives to a module namespace and that record/replay works end-to-end.
"""
import time

from retracesoftware.proxy.contexts import record_context, replay_context
from retracesoftware.proxy.system import System
from retracesoftware.install.installation import Installation
from retracesoftware.install.patcher import patch
from retracesoftware.proxy.messagestream import MemoryWriter


def test_patch_proxy_type_record_replay():
    """patch(spec={'proxy': [type_name]}) patches a type, record/replay works."""

    class Timer:
        """Fake C-extension-style type whose methods should be proxied."""
        def now(self):
            return time.time()

    # Build a fake module namespace
    fake_module = {'Timer': Timer, '__name__': 'fake_timer'}

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    # Patch using the new patch() function
    patch(fake_module, {'proxy': ['Timer']}, Installation(system))

    assert Timer in system.patched_types

    writer = MemoryWriter()

    with record_context(system, writer):
        obj = Timer()
        t = obj.now()

    assert isinstance(t, float)
    assert len(writer.tape) > 0, "now() result should be recorded"
    assert 'RESULT' in writer.tape


def test_patch_immutable():
    """patch(spec={'immutable': [type_name]}) adds types to immutable_types."""
    fake_module = {
        'MyError': ValueError,
        'MyTimeout': TimeoutError,
        '__name__': 'fake_errors',
    }

    system = System()
    patch(fake_module, {'immutable': ['MyError', 'MyTimeout']}, Installation(system))

    assert ValueError in system.immutable_types
    assert TimeoutError in system.immutable_types


def test_patch_disable():
    """patch(spec={'disable': [name]}) wraps function with disable_for.

    disable_for returns a sequence that clears both gates before
    calling the original.  We just verify the namespace value is
    replaced (the original function reference is no longer there).
    """

    def loud_print(*args):
        return args

    fake_module = {'loud_print': loud_print, '__name__': 'fake_io'}

    system = System()
    patch(fake_module, {'disable': ['loud_print']}, Installation(system))

    # Should be replaced with a disable_for wrapper
    assert fake_module['loud_print'] is not loud_print


def test_patch_combined_spec():
    """Multiple directives in one spec are all applied."""

    class NetType:
        def connect(self, addr):
            return f"connected to {addr}"

    fake_module = {
        'NetType': NetType,
        'error': OSError,
        '__name__': 'fake_net',
    }

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    spec = {
        'proxy': ['NetType'],
        'immutable': ['error'],
    }

    patch(fake_module, spec, Installation(system))

    assert NetType in system.patched_types
    assert OSError in system.immutable_types

    # Record/replay the patched type
    writer = MemoryWriter()
    obj = NetType()

    with record_context(system, writer):
        result = obj.connect('localhost')

    assert result == 'connected to localhost'

    with replay_context(system, writer.reader()):
        result2 = obj.connect('localhost')

    assert result2 == result


def test_unbound_instance_passthrough():
    """Methods on unbound instances pass through without recording.

    When an instance of a patched type is created outside any record
    context, it is not bound in the writer's binding table.  Calling
    a method on it inside a record context should execute the real
    method directly and produce no tape entries.
    """

    class Sensor:
        def read(self):
            return 42

    fake_module = {'Sensor': Sensor, '__name__': 'fake_sensor'}

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patch(fake_module, {'proxy': ['Sensor']}, Installation(system))

    assert Sensor in system.patched_types

    # Instance created OUTSIDE any context — not bound.
    unbound = Sensor()
    writer = MemoryWriter()

    with record_context(system, writer):
        result = unbound.read()

    assert result == 42, "unbound instance should call real method"
    assert len(writer.tape) == 0, (
        f"unbound instance should not produce tape entries, got {writer.tape}")


def test_bound_instance_recorded():
    """Methods on bound instances ARE recorded.

    When an instance of a patched type is created inside a record
    context, it is bound and its methods route through the executor.
    """

    class Sensor:
        def read(self):
            return 42

    fake_module = {'Sensor': Sensor, '__name__': 'fake_sensor'}

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patch(fake_module, {'proxy': ['Sensor']}, Installation(system))

    writer = MemoryWriter()

    with record_context(system, writer):
        bound = Sensor()
        result = bound.read()

    assert result == 42, "bound instance should return real value during record"
    assert len(writer.tape) > 0, "bound instance method call should be recorded"
    assert 'RESULT' in writer.tape, "tape should contain a RESULT entry"


def test_mixed_bound_and_unbound_patched_args_passthrough():
    """Mixed retraced/live patched arguments should fall through to the real method.

    A bound receiver plus an unbound patched argument is a mixed-state call. The
    method must not route through the external gate, otherwise record/replay would
    operate against a partially live object graph.
    """

    class Sensor:
        def __init__(self, value):
            self.value = value

        def combine(self, other):
            return (self.value, other.value)

    fake_module = {'Sensor': Sensor, '__name__': 'fake_sensor'}

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patch(fake_module, {'proxy': ['Sensor']}, Installation(system))

    live = Sensor("live")
    writer = MemoryWriter()

    with record_context(system, writer):
        bound = Sensor("bound")
        tape_before_call = len(writer.tape)
        result = bound.combine(live)
        tape_after_call = len(writer.tape)

    assert result == ("bound", "live")
    assert tape_after_call == tape_before_call, (
        "mixed bound/unbound patched call should not be recorded"
    )


def test_mixed_bound_and_unbound_unhashable_patched_args_passthrough():
    """Mixed-state passthrough should handle unhashable patched instances."""

    class Sensor:
        __hash__ = None

        def __init__(self, value):
            self.value = value

        def combine(self, other):
            return (self.value, other.value)

    fake_module = {'Sensor': Sensor, '__name__': 'fake_sensor'}

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patch(fake_module, {'proxy': ['Sensor']}, Installation(system))

    live = Sensor("live")
    writer = MemoryWriter()

    with record_context(system, writer):
        bound = Sensor("bound")
        tape_before_call = len(writer.tape)
        result = bound.combine(live)
        tape_after_call = len(writer.tape)

    assert result == ("bound", "live")
    assert tape_after_call == tape_before_call, (
        "mixed bound/unbound patched call should not be recorded"
    )


def test_pathparam_predicate_false_returns_unbound():
    """pathparam + predicate returning False → instance unbound, methods pass through.

    Simulates the _io.toml pattern: a factory function (open) has a
    pathparam directive on its 'file' argument.  When the predicate
    returns False (path not on whitelist), disable_for runs the factory
    with gates disabled, so the returned instance is not bound.
    Subsequent method calls on it should NOT be recorded.
    """
    call_log = []

    class FakeFileIO:
        def __init__(self, file, mode='r'):
            self.name = file
            self.mode = mode

        def read(self):
            call_log.append(('read', self.name))
            return f"data from {self.name}"

    def fake_open(file, mode='r'):
        return FakeFileIO(file, mode)

    fake_module = {
        'FakeFileIO': FakeFileIO,
        'open': fake_open,
        '__name__': 'fake_io',
    }

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    predicate = lambda path: str(path).startswith('/dev/')

    patch(
        fake_module,
        {
            'proxy': ['FakeFileIO', 'open'],
            'pathparam': {'open': 'file'},
        },
        Installation(system),
        pathpredicate=predicate,
    )

    writer = MemoryWriter()

    with record_context(system, writer):
        # Path NOT on whitelist → predicate returns False → passthrough
        f = fake_module['open'](file='/tmp/foo.txt')
        data = f.read()

    assert data == "data from /tmp/foo.txt"
    assert not system.is_bound(f), "instance from non-matching path should NOT be bound"
    assert len(writer.tape) == 0, (
        f"non-matching path should produce no tape entries, got {writer.tape}")


def test_pathparam_predicate_true_returns_bound():
    """pathparam + predicate returning True → instance bound, methods recorded.

    Same setup as above, but the path matches the predicate so the
    factory runs normally through the gate.  The instance IS bound
    and method calls ARE recorded.
    """
    class FakeFileIO:
        def __init__(self, file, mode='r'):
            self.name = file
            self.mode = mode

        def read(self):
            return f"data from {self.name}"

    def fake_open(file, mode='r'):
        return FakeFileIO(file, mode)

    fake_module = {
        'FakeFileIO': FakeFileIO,
        'open': fake_open,
        '__name__': 'fake_io',
    }

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    predicate = lambda path: str(path).startswith('/dev/')

    patch(
        fake_module,
        {
            'proxy': ['FakeFileIO', 'open'],
            'pathparam': {'open': 'file'},
        },
        Installation(system),
        pathpredicate=predicate,
    )

    writer = MemoryWriter()

    with record_context(system, writer):
        # Path ON whitelist → predicate returns True → retraced
        f = fake_module['open'](file='/dev/null')
        data = f.read()

    assert data == "data from /dev/null"
    assert system.is_bound(f), "instance from matching path should be bound"
    assert 'RESULT' in writer.tape, "matching path should produce tape entries"


def test_patch_proxy_function_record_replay():
    """patch(spec={'proxy': [fn_name]}) wraps a function via patch_function.

    The wrapped function routes through the external gate, so it is
    recorded during record and replayed during replay.
    """
    counter = 0

    def get_count():
        nonlocal counter
        counter += 1
        return counter

    fake_module = {'get_count': get_count, '__name__': 'fake_counter'}

    system = System()
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    patch(fake_module, {'proxy': ['get_count']}, Installation(system))

    # Should be replaced with a patch_function wrapper
    assert fake_module['get_count'] is not get_count

    # Without context — direct call, counter advances
    assert fake_module['get_count']() == 1
    assert fake_module['get_count']() == 2

    writer = MemoryWriter()

    # Record — get_count goes through the gate, result is stored
    with record_context(system, writer):
        recorded = fake_module['get_count']()

    assert recorded == 3
    assert 'RESULT' in writer.tape

    # Replay — get_count returns stored value, counter does NOT advance
    old_counter = counter
    with replay_context(system, writer.reader()):
        replayed = fake_module['get_count']()

    assert replayed == recorded, f"replay should return {recorded}, got {replayed}"
    assert counter == old_counter, "counter should not advance during replay"
