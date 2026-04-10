"""Test the TestRunner API itself.

Verifies record/replay/run semantics, divergence detection, and
error handling.
"""
import socket

import pytest

from retracesoftware.install import ReplayDivergence


def test_run_returns_value(runner):
    """run() returns the recorded return value."""
    def do_hostname():
        return socket.gethostname()

    result = runner.run(do_hostname)
    assert isinstance(result, str)
    assert len(result) > 0


def test_record_then_replay(runner):
    """Separate record() and replay() calls work correctly."""
    def do_hostname():
        return socket.gethostname()

    recording = runner.record(do_hostname)
    assert recording.result is not None
    assert recording.error is None

    result = runner.replay(recording, do_hostname)
    assert result == recording.result


def test_diagnose_simple(runner):
    """Smoke test: diagnose() with a trivial function that shouldn't diverge."""
    def do_hostname():
        return socket.gethostname()

    runner.diagnose(do_hostname)


def test_recording_captures_result(runner):
    """Recording captures the result of the function call."""
    def do_hostname():
        return socket.gethostname()

    recording = runner.record(do_hostname)
    assert recording.result is not None
    assert recording.error is None


def test_proxied_return_value():
    """A patched method returns a non-immutable type — proxied during record/replay.

    Uses a standalone System where list is NOT in immutable_types, so
    the return value from query() triggers the proxy factory path:
    should_proxy(list) → True → wrapped in a DynamicProxy during record,
    unwrapped during replay.

    Uses a subclass (Repo) of the patched base (Database) to avoid
    the pre-existing set_on_alloc issue with direct instantiation of
    the patched type outside a context.
    """
    from retracesoftware.proxy.system import System
    from retracesoftware.install import TestRunner

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

    test_runner = TestRunner(system)
    db = Repo()

    result = test_runner.run(lambda: db.query())
    assert result == [1, 2, 3], f"run() should return [1, 2, 3], got {result}"
    assert not system.is_bound(db), "run() should restore bound state after replay"
