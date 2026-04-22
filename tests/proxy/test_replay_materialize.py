from argparse import Namespace
import _thread

import pytest

from retracesoftware.__main__ import install_and_run
from retracesoftware.install.patcher import patch
from retracesoftware.install.installation import Installation
from retracesoftware.proxy._system_adapters import _run_with_replay
from retracesoftware.proxy.io import recorder, replayer
from retracesoftware.proxy.system import System
from retracesoftware.testing.memorytape import IOMemoryTape
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


def test_install_and_run_allocate_lock_minimal_replay_materialize_regression():
    tape = IOMemoryTape()

    options = Namespace(
        monitor=0,
        retrace_file_patterns=None,
        verbose=False,
        trace_shutdown=False,
    )

    def configure(system):
        system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})

    def allocate_and_acquire():
        lock = _thread.allocate_lock()
        acquired = lock.acquire(False)
        if acquired:
            lock.release()
        return acquired

    record_system = recorder(writer=tape.writer().write, debug=False, stacktraces=False)
    configure(record_system)
    assert install_and_run(system=record_system, options=options, function=allocate_and_acquire) is True

    replay_reader = tape.reader()
    replay_system = replayer(
        next_object=replay_reader.read,
        close=getattr(replay_reader, "close", None),
        debug=False,
        stacktraces=False,
    )
    configure(replay_system)

    assert install_and_run(system=replay_system, options=options, function=allocate_and_acquire) is True
