from retracesoftware.install.patcher import patch
from retracesoftware.proxy.system import System, _run_with_replay


def test_patch_registers_replay_materialize_callables_and_undoes():
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
    undo = patch(namespace, {"replay_materialize": ["allocate_lock", "MemoryBIO"]}, system)

    assert allocate_lock in system.replay_materialize
    assert MemoryBIO in system.replay_materialize

    undo()

    assert allocate_lock not in system.replay_materialize
    assert MemoryBIO not in system.replay_materialize


def test_patch_registers_original_callable_when_proxy_and_replay_materialize_overlap():
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
        system,
    )

    assert allocate_lock in system.replay_materialize
    assert namespace["allocate_lock"] not in system.replay_materialize

    undo()

    assert allocate_lock not in system.replay_materialize


def test_run_with_replay_always_returns_recorded_result_for_selected_functions():
    trace_result = object()
    live_result = object()

    def allocate_lock():
        return live_result

    replay = _run_with_replay(
        lambda: trace_result,
        replay_materialize={allocate_lock},
        materialize=lambda fn, *args, **kwargs: fn(*args, **kwargs),
    )

    assert replay(allocate_lock) is trace_result


def test_run_with_replay_always_returns_recorded_result_for_patched_functions():
    trace_result = object()
    live_result = object()
    system = System()

    def allocate_lock():
        return live_result

    patched = system.patch_function(allocate_lock)
    replay = _run_with_replay(
        lambda: trace_result,
        replay_materialize={allocate_lock},
        materialize=lambda fn, *args, **kwargs: fn(*args, **kwargs),
    )

    assert replay(patched) is trace_result


def test_run_with_replay_always_returns_recorded_result_for_selected_types():
    trace_result = object()

    class MemoryBIO:
        def __init__(self):
            self.live = True

    replay = _run_with_replay(
        lambda: trace_result,
        replay_materialize={MemoryBIO},
        materialize=lambda fn, *args, **kwargs: fn(*args, **kwargs),
    )

    assert replay(MemoryBIO) is trace_result


def test_run_with_replay_does_not_materialize_unselected_functions():
    trace_result = object()

    def allocate_lock():
        return object()

    replay = _run_with_replay(
        lambda: trace_result,
        replay_materialize=set(),
        materialize=lambda fn, *args, **kwargs: fn(*args, **kwargs),
    )

    assert replay(allocate_lock) is trace_result


def test_run_with_replay_does_not_materialize_before_recorded_error():
    class RecordedFailure(RuntimeError):
        pass

    called = False

    def allocate_lock():
        return object()

    def materialize(fn, *args, **kwargs):
        nonlocal called
        called = True
        return fn(*args, **kwargs)

    replay = _run_with_replay(
        lambda: (_ for _ in ()).throw(RecordedFailure("boom")),
        replay_materialize={allocate_lock},
        materialize=materialize,
    )

    try:
        replay(allocate_lock)
    except RecordedFailure:
        pass
    else:
        assert False, "expected recorded failure to be raised"

    assert called is False
