from retracesoftware.install.patcher import patch
from retracesoftware.install.installation import Installation
from retracesoftware.proxy._system_adapters import _run_with_replay
from retracesoftware.proxy.system import System


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
