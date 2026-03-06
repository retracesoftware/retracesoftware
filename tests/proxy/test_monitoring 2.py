"""Tests for sys.monitoring-based divergence detection.

Exercises the full monitoring stack:
  - MonitorMessage parsing in MessageStream
  - monitor_checkpoint verification
  - install_monitoring callback registration
  - TestRunner integration with monitor= parameter
"""
import sys
import pytest

from retracesoftware.proxy.messagestream import (
    MessageStream, MonitorMessage, MemoryWriter, MemoryReader,
)


# ── MonitorMessage unit tests ─────────────────────────────────

class TestMonitorMessage:
    def test_parse_monitor_tag(self):
        tape = ['MONITOR', 'S:foo', 'SYNC']
        ms = MessageStream(iter(tape).__next__)
        msg = ms._next_message()
        assert isinstance(msg, MonitorMessage)
        assert msg.value == 'S:foo'

    def test_monitor_checkpoint_match(self):
        tape = ['MONITOR', 'S:foo']
        ms = MessageStream(iter(tape).__next__, monitor_enabled=True)
        ms.monitor_checkpoint('S:foo')

    def test_monitor_checkpoint_mismatch(self):
        from retracesoftware.install import ReplayDivergence
        tape = ['MONITOR', 'S:foo']
        ms = MessageStream(iter(tape).__next__, monitor_enabled=True)
        with pytest.raises(ReplayDivergence, match='monitor divergence'):
            ms.monitor_checkpoint('S:bar')

    def test_monitor_checkpoint_wrong_message_type(self):
        from retracesoftware.install import ReplayDivergence
        tape = ['SYNC']
        ms = MessageStream(iter(tape).__next__, monitor_enabled=True)
        with pytest.raises(ReplayDivergence, match='expected MONITOR'):
            ms.monitor_checkpoint('S:foo')


# ── Skip behavior tests ──────────────────────────────────────

class TestMonitorSkip:
    def test_sync_skips_monitor_when_disabled(self):
        tape = ['MONITOR', 'S:foo', 'MONITOR', 'R:foo', 'SYNC']
        ms = MessageStream(iter(tape).__next__, monitor_enabled=False)
        ms.sync()

    def test_sync_raises_on_monitor_when_enabled(self):
        from retracesoftware.install import ReplayDivergence
        tape = ['MONITOR', 'S:foo', 'SYNC']
        ms = MessageStream(iter(tape).__next__, monitor_enabled=True)
        with pytest.raises(ReplayDivergence, match='unexpected MONITOR'):
            ms.sync()

    def test_result_skips_monitor_when_disabled(self):
        tape = ['MONITOR', 'S:foo', 'RESULT', 42]
        ms = MessageStream(iter(tape).__next__, monitor_enabled=False)
        assert ms.result() == 42

    def test_result_raises_on_monitor_when_enabled(self):
        from retracesoftware.install import ReplayDivergence
        tape = ['MONITOR', 'S:foo', 'RESULT', 42]
        ms = MessageStream(iter(tape).__next__, monitor_enabled=True)
        with pytest.raises(ReplayDivergence, match='unexpected MONITOR'):
            ms.result()

    def test_checkpoint_skips_monitor_when_disabled(self):
        tape = ['MONITOR', 'S:foo', 'CHECKPOINT', 'ok']
        ms = MessageStream(iter(tape).__next__, monitor_enabled=False)
        ms.checkpoint('ok')

    def test_checkpoint_raises_on_monitor_when_enabled(self):
        from retracesoftware.install import ReplayDivergence
        tape = ['MONITOR', 'S:foo', 'CHECKPOINT', 'ok']
        ms = MessageStream(iter(tape).__next__, monitor_enabled=True)
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


# ── TestRunner integration ────────────────────────────────────

@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="sys.monitoring requires Python 3.12+")
class TestRunnerMonitoring:
    """Test monitoring through the TestRunner (in-process record+replay)."""

    @pytest.fixture(scope="class")
    def runner(self):
        from retracesoftware.install import install_for_pytest
        return install_for_pytest(modules=["socket"])

    def test_run_with_monitor_level_1(self, runner):
        """Record+replay with monitor=1 — should not raise."""
        import socket
        def do_dns():
            return socket.getaddrinfo("localhost", 80)
        runner.run(do_dns, monitor=1)

    def test_record_replay_with_monitor_level_1(self, runner):
        """Separate record/replay with monitor=1."""
        import socket
        def do_dns():
            return socket.getaddrinfo("localhost", 80)
        recording = runner.record(do_dns, monitor=1)
        runner.replay(recording, do_dns, monitor=1)

    def test_monitor_tape_has_monitor_messages(self, runner):
        """Verify MONITOR messages appear on the tape when monitor > 0."""
        import socket
        def do_dns():
            return socket.getaddrinfo("localhost", 80)
        recording = runner.record(do_dns, monitor=1)
        assert 'MONITOR' in recording.tape, \
            "Expected MONITOR messages in tape with monitor=1"

    def test_no_monitor_messages_at_level_0(self, runner):
        """Verify NO MONITOR messages at level 0 (zero overhead)."""
        import socket
        def do_dns():
            return socket.getaddrinfo("localhost", 80)
        recording = runner.record(do_dns, monitor=0)
        assert 'MONITOR' not in recording.tape, \
            "Expected no MONITOR messages at level 0"
