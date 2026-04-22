"""Tests for sys.monitoring-based divergence detection.

Exercises the full monitoring stack:
  - MonitorMessage parsing in next_message
  - monitor_checkpoint verification
  - install_monitoring callback registration
  - Runner integration with monitor= parameter
"""
import sys
import pytest

from retracesoftware.protocol.messages import MonitorMessage
from retracesoftware.protocol.replay import ReplayReader, StacktraceFactory, next_message
from retracesoftware.testing.memorytape import MemoryWriter, MemoryReader
from tests.runner import Runner


class FakeStackFactory:
    def __init__(self, *deltas):
        self._deltas = list(deltas)

    def delta(self):
        return self._deltas.pop(0)


# ── MonitorMessage unit tests ─────────────────────────────────

class TestMonitorMessage:
    def test_parse_monitor_tag(self):
        tape = ['MONITOR', 'S:foo', 'SYNC']
        msg = next_message(
            iter(tape).__next__,
            stacktrace_factory=lambda *_: None,
            thread_id=lambda: ("monitor",),
        )
        assert isinstance(msg, MonitorMessage)
        assert msg.value == 'S:foo'
        assert msg.thread_id == ("monitor",)

    def test_monitor_checkpoint_match(self):
        tape = ['MONITOR', 'S:foo']
        ms = ReplayReader(iter(tape).__next__, bind=lambda obj: None, monitor_enabled=True)
        ms.monitor_checkpoint('S:foo')

    def test_monitor_checkpoint_mismatch(self):
        from retracesoftware.install import ReplayDivergence
        tape = ['MONITOR', 'S:foo']
        ms = ReplayReader(iter(tape).__next__, bind=lambda obj: None, monitor_enabled=True)
        with pytest.raises(ReplayDivergence, match='monitor divergence'):
            ms.monitor_checkpoint('S:bar')

    def test_monitor_checkpoint_wrong_message_type(self):
        from retracesoftware.install import ReplayDivergence
        tape = ['SYNC']
        ms = ReplayReader(iter(tape).__next__, bind=lambda obj: None, monitor_enabled=True)
        with pytest.raises(ReplayDivergence, match='expected MONITOR'):
            ms.monitor_checkpoint('S:foo')


# ── Skip behavior tests ──────────────────────────────────────

class TestMonitorSkip:
    def test_sync_skips_monitor_when_disabled(self):
        tape = ['MONITOR', 'S:foo', 'MONITOR', 'R:foo', 'SYNC']
        ms = ReplayReader(iter(tape).__next__, bind=lambda obj: None, monitor_enabled=False)
        ms.sync()

    def test_sync_raises_on_monitor_when_enabled(self):
        from retracesoftware.install import ReplayDivergence
        tape = ['MONITOR', 'S:foo', 'SYNC']
        ms = ReplayReader(iter(tape).__next__, bind=lambda obj: None, monitor_enabled=True)
        with pytest.raises(ReplayDivergence, match='unexpected MONITOR'):
            ms.sync()

    def test_result_skips_monitor_when_disabled(self):
        tape = ['MONITOR', 'S:foo', 'RESULT', 42]
        ms = ReplayReader(iter(tape).__next__, bind=lambda obj: None, monitor_enabled=False)
        assert ms.read_result() == 42

    def test_result_raises_on_monitor_when_enabled(self):
        from retracesoftware.install import ReplayDivergence
        tape = ['MONITOR', 'S:foo', 'RESULT', 42]
        ms = ReplayReader(iter(tape).__next__, bind=lambda obj: None, monitor_enabled=True)
        with pytest.raises(ReplayDivergence, match='unexpected MONITOR'):
            ms.read_result()

    def test_checkpoint_skips_monitor_when_disabled(self):
        tape = ['MONITOR', 'S:foo', StacktraceFactory().materialize(0, ()), 'CHECKPOINT', 'ok']
        ms = ReplayReader(
            iter(tape).__next__,
            bind=lambda obj: None,
            monitor_enabled=False,
            stacktrace_factory=FakeStackFactory((0, ())),
        )
        ms.checkpoint('ok')

    def test_checkpoint_raises_on_monitor_when_enabled(self):
        from retracesoftware.install import ReplayDivergence
        tape = ['MONITOR', 'S:foo', StacktraceFactory().materialize(0, ()), 'CHECKPOINT', 'ok']
        ms = ReplayReader(
            iter(tape).__next__,
            bind=lambda obj: None,
            monitor_enabled=True,
            stacktrace_factory=FakeStackFactory((0, ())),
        )
        with pytest.raises(ReplayDivergence, match='unexpected MONITOR'):
            ms.checkpoint('ok')


# ── MemoryWriter / MemoryReader round-trip ────────────────────

class TestMemoryWriterReader:
    def test_monitor_event_roundtrip(self):
        w = MemoryWriter()
        w.sync()
        w.monitor_event('S:foo')
        w.monitor_event('R:foo')
        w.sync()
        w.write_result(42)

        r = MemoryReader(w.tape, monitor_enabled=False)
        r.sync()
        r.sync()
        assert r.read_result() == 42

    def test_monitor_event_roundtrip_with_verify(self):
        w = MemoryWriter()
        w.sync()
        w.monitor_event('S:foo')
        w.monitor_event('R:foo')
        w.sync()
        w.write_result(42)

        r = MemoryReader(w.tape, monitor_enabled=True)
        r.sync()
        r.monitor_checkpoint('S:foo')
        r.monitor_checkpoint('R:foo')
        r.sync()
        assert r.read_result() == 42


# ── Runner integration ────────────────────────────────────────

@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="sys.monitoring requires Python 3.12+")
class TestRunnerMonitoring:
    """Test monitoring through the new in-process Runner."""

    def test_run_with_monitor_level_1(self):
        """Record+replay with monitor=1 — should not raise."""
        import socket
        runner = Runner(monitor=1)

        def do_dns():
            return socket.getaddrinfo("localhost", 80)

        runner.run(do_dns)

    def test_record_replay_with_monitor_level_1(self):
        """Separate record/replay with monitor=1."""
        import socket
        runner = Runner(monitor=1)

        def do_dns():
            return socket.getaddrinfo("localhost", 80)

        recording = runner.record(do_dns)
        runner.replay(recording, do_dns)

    def test_monitor_tape_has_monitor_messages(self):
        """Verify MONITOR messages appear on the tape when monitor > 0."""
        import socket
        runner = Runner(monitor=1)

        def do_dns():
            return socket.getaddrinfo("localhost", 80)

        recording = runner.record(do_dns)
        assert 'MONITOR' in recording.tape, \
            "Expected MONITOR messages in tape with monitor=1"

    def test_no_monitor_messages_at_level_0(self):
        """Verify NO MONITOR messages at level 0 (zero overhead)."""
        import socket
        runner = Runner(monitor=0)

        def do_dns():
            return socket.getaddrinfo("localhost", 80)

        recording = runner.record(do_dns)
        assert 'MONITOR' not in recording.tape, \
            "Expected no MONITOR messages at level 0"
