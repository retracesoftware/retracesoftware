import pytest

from retracesoftware.proxy.io import recorder
from retracesoftware.proxy.patchtype import patch_type
from retracesoftware.proxy.system import System
from retracesoftware.testing.memorytape import IOMemoryTape

def _run_with_replay(ext_runner):
    def replay_fn(fn, *args, **kwargs):
        return ext_runner()

    return replay_fn


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

    def fn():
        nonlocal called
        called = True

    replay = _run_with_replay(lambda: (_ for _ in ()).throw(RecordedFailure("boom")))

    try:
        replay(fn)
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
