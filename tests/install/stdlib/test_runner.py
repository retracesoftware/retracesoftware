"""Test the Runner API itself.

Verifies record/replay/run semantics, divergence detection, and
error handling.
"""
import socket
import sys

import pytest

from retracesoftware.install import ReplayDivergence
from tests.runner import DEFAULT_RUN_MATRIX, Runner, retrace_test


@retrace_test
def test_run_returns_value():
    """run() returns the recorded return value."""
    result = socket.gethostname()
    assert isinstance(result, str)
    assert len(result) > 0
    return result


def test_record_then_replay():
    """Separate record() and replay() calls work correctly."""
    runner = Runner()

    def do_hostname():
        return socket.gethostname()

    recording = runner.record(do_hostname)
    assert recording.result is not None
    assert recording.error is None

    result = runner.replay(recording, do_hostname)
    assert result == recording.result


def test_recording_captures_result():
    """Recording captures the result of the function call."""
    runner = Runner()

    def do_hostname():
        return socket.gethostname()

    recording = runner.record(do_hostname)
    assert recording.result is not None
    assert recording.error is None


def test_runner_accepts_core_matrix_preset():
    """Runner should accept the named core matrix preset."""
    runner = Runner(matrix="core")

    def do_hostname():
        return socket.gethostname()

    result = runner.run(do_hostname)
    assert isinstance(result, str)


@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="sys.monitoring requires Python 3.12+",
)
def test_record_supports_monitor_level_1():
    """record() should still work when monitoring is enabled."""
    runner = Runner(monitor=1)

    def do_hostname():
        return socket.gethostname()

    recording = runner.record(do_hostname)
    assert recording.error is None
    assert "MONITOR" in recording.tape


def test_proxied_return_value():
    """A patched method returns a non-immutable type — proxied during record/replay.

    Uses a standalone System where list is NOT in immutable_types, so
    the return value from query() triggers the proxy factory path:
    should_proxy(list) → True → wrapped in a DynamicProxy during record,
    unwrapped during replay.

    The receiver is allocated inside the runner-managed contexts so it
    is bound on both record and replay, matching the supported System
    contract exercised by the proxy-level tests.
    """
    class Database:
        """Base type to be patched — methods become external."""
        def query(self):
            return [1, 2, 3]

    class Repo(Database):
        """Subclass — inherits query() as external."""
        pass

    systems = []

    def configure_system(system):
        systems.append(system)
        system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
        system.patch_type(Database)

    runner = Runner(configure_system=configure_system)
    seen = []

    def work():
        db = Repo()
        seen.append(db)
        return db.query()

    result = runner.run(work)
    assert result == [1, 2, 3], f"run() should return [1, 2, 3], got {result}"
    assert len(systems) == len(seen) == 2 * len(DEFAULT_RUN_MATRIX)
    assert all(system.patched_types == set() for system in systems), (
        "run() should restore patched type state after each pass"
    )


def test_runner_replays_external_allocation_results():
    """Runner should round-trip patched objects allocated inside external calls."""
    class Base:
        def peers(self):
            return [type(self)(), type(self)()]

    class Box(Base):
        pass

    def configure_system(system):
        system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
        system.patch_type(Base)

    runner = Runner(configure_system=configure_system, matrix=[{"name": "default"}])

    def work():
        root = Box()
        items = root.peers()
        return len(items), tuple(isinstance(item, Base) for item in items)

    recording = runner.record(work)

    assert recording.result == (2, (True, True))
    assert recording.error is None

    tags = [type(item).__name__ if not isinstance(item, str) else item for item in recording.tape]
    assert "NEW_BINDING" in tags
    assert "CALLBACK" in tags
    assert "CALLBACK_RESULT" in tags

    assert runner.replay(recording, work) == recording.result


def test_run_reruns_divergence_with_debug_tape():
    """run() should retry divergences with debug/stacktrace diagnostics."""
    state = {"calls": 0}

    class Base:
        def value(self):
            return "side-effect"

    class Box(Base):
        pass

    def configure_system(system):
        system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
        system.patch_type(Base)

    runner = Runner(configure_system=configure_system)

    def work():
        assert Box().value() == "side-effect"
        state["calls"] += 1
        return "record" if state["calls"] % 2 else "replay"

    with pytest.raises(ReplayDivergence) as excinfo:
        runner.run(work)

    assert "CHECKPOINT" in excinfo.value.tape
    assert any("Automatic diagnostic rerun" in note for note in excinfo.value.__notes__)
