from argparse import Namespace
import _thread

import pytest

from retracesoftware.__main__ import install_and_run
from retracesoftware.install.patcher import patch
from retracesoftware.install.installation import Installation
from retracesoftware.proxy.io import recorder, replayer
from retracesoftware.proxy.patchtype import patch_type
from retracesoftware.proxy.system import System
from retracesoftware.testing.memorytape import IOMemoryTape


def _run_with_replay(ext_runner):
    def replay_fn(fn, *args, **kwargs):
        return ext_runner()

    return replay_fn


def test_patch_ignores_replay_materialize_when_system_has_no_registry():
    system = System()

    def allocate_lock():
        return object()

    class MemoryBIO:
        pass

    namespace = {
        "__name__": "_thread",
        "allocate_lock": allocate_lock,
        "MemoryBIO": MemoryBIO,
    }
    undo = patch(
        namespace,
        {"replay_materialize": ["allocate_lock", "MemoryBIO"]},
        Installation(system),
    )

    undo()


def test_patch_proxy_and_replay_materialize_overlap_is_a_no_op_without_registry():
    system = System()

    def allocate_lock():
        return object()

    namespace = {
        "__name__": "_thread",
        "allocate_lock": allocate_lock,
    }
    undo = patch(
        namespace,
        {"proxy": ["allocate_lock"], "replay_materialize": ["allocate_lock"]},
        Installation(system),
    )

    undo()


def test_patch_type_is_idempotent_for_subtypes_patched_through_base():
    bound = []
    system = System(on_bind=bound.append)

    class Base:
        def ping(self):
            return "base"

    class Child(Base):
        def ping(self):
            return "child"

    try:
        patch_type(system, Base)
        assert Base in system.patched_types
        assert Child in system.patched_types

        bound_after_base_patch = list(bound)

        assert patch_type(system, Child) is Child
        assert system.patch(Child) is Child
        assert bound == bound_after_base_patch
    finally:
        system.unpatch_types()


def test_patch_replay_materialize_tracks_patched_callable_identity():
    class FakeSystem:
        def __init__(self):
            self.replay_materialize = set()

        def patch(self, value, install_session=None):
            def patched(*args, **kwargs):
                return value(*args, **kwargs)

            patched.__name__ = getattr(value, "__name__", "patched")
            return patched

    replay_system = FakeSystem()

    def allocate_lock():
        return object()

    namespace = {
        "__name__": "_thread",
        "allocate_lock": allocate_lock,
    }
    undo = patch(
        namespace,
        {"proxy": ["allocate_lock"], "replay_materialize": ["allocate_lock"]},
        Installation(replay_system),
    )
    try:
        patched = namespace["allocate_lock"]
        assert patched is not allocate_lock
        assert patched in replay_system.replay_materialize
        assert allocate_lock not in replay_system.replay_materialize
    finally:
        undo()


def test_run_with_replay_returns_ext_runner_value():
    trace_result = object()
    called = []

    def fn(*args, **kwargs):
        called.append((args, kwargs))
        return object()

    replay = _run_with_replay(lambda: trace_result)

    assert replay(fn, 1, 2, name="value") is trace_result
    assert called == []


def test_run_with_replay_propagates_recorded_error_without_calling_live_function():
    class RecordedFailure(RuntimeError):
        pass

    called = False

    def allocate_lock():
        return object()

    replay = _run_with_replay(lambda: (_ for _ in ()).throw(RecordedFailure("boom")))

    try:
        replay(allocate_lock)
    except RecordedFailure:
        pass
    else:
        assert False, "expected recorded failure to be raised"

    assert called is False


def test_recorder_async_new_patched_rejects_unpatched_types():
    tape = IOMemoryTape()
    system = recorder(writer=tape.writer().write, debug=False, stacktraces=False)
    try:
        with pytest.raises(AssertionError, match="async_new_patched expected a patched type"):
            system.async_new_patched(object())
    finally:
        system.unpatch_types()


def test_replay_materialize_disabled_call_does_not_consume_next_recorded_result():
    tape = IOMemoryTape()

    options = Namespace(
        monitor=0,
        retrace_file_patterns=None,
        verbose=False,
        trace_shutdown=False,
    )

    def configure(system):
        system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    def allocate_and_acquire(system):
        skipped_allocate = system.disable_for(_thread.allocate_lock)

        skipped_lock = skipped_allocate()
        skipped_acquired = skipped_lock.acquire(False)
        if skipped_acquired:
            skipped_lock.release()

        lock = _thread.allocate_lock()
        acquired = lock.acquire(False)
        if acquired:
            lock.release()
        return skipped_acquired, acquired

    record_system = recorder(writer=tape.writer().write, debug=False, stacktraces=False)
    configure(record_system)
    assert install_and_run(
        system=record_system,
        options=options,
        function=allocate_and_acquire,
        args=(record_system,),
    ) == (True, True)

    replay_reader = tape.reader()
    replay_system = replayer(
        next_object=replay_reader.read,
        close=getattr(replay_reader, "close", None),
        debug=False,
        stacktraces=False,
    )
    configure(replay_system)

    assert install_and_run(
        system=replay_system,
        options=options,
        function=allocate_and_acquire,
        args=(replay_system,),
    ) == (True, True)
