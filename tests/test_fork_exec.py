"""Whole-process tests for fork and exec under retrace.

Each test records a script via ``python -m retracesoftware``, then
replays it, and asserts that:

  1. Record succeeds (exit code 0).
  2. Replay succeeds (exit code 0).
  3. Replay stdout matches record stdout — proving deterministic replay.

Note: all tests are currently marked ``xfail`` because ``__main__.py``
still uses the old ``RecordProxySystem`` / ``ReplayProxySystem`` which
have not been migrated to the new gate-based system.  These tests
define the target behaviour and will pass once the migration is done.
"""
import os
import sys
import json
from pathlib import Path

import pytest

from conftest import run_record, run_replay


SCRIPTS = Path(__file__).parent / "scripts"

# All tests xfail until __main__.py is migrated to the gate-based system
pytestmark = pytest.mark.xfail(
    reason="__main__.py still uses old RecordProxySystem (not yet migrated)",
    strict=True,
)


# ── helpers ────────────────────────────────────────────────────────

def record_and_replay(tmpdir, script_name, extra_record_args=None):
    """Record a script, then replay it.  Return (record_proc, replay_proc)."""
    script = SCRIPTS / script_name
    recording = os.path.join(tmpdir, "recording")

    rec = run_record(script, recording, extra_args=extra_record_args)
    if rec.returncode != 0:
        pytest.fail(
            f"Record failed (exit {rec.returncode}):\n"
            f"stdout: {rec.stdout}\nstderr: {rec.stderr}"
        )

    rep = run_replay(recording)
    return rec, rep


# ── tests ──────────────────────────────────────────────────────────

class TestSimpleRecordReplay:
    """Baseline: record and replay a trivial script."""

    def test_record_succeeds(self, tmpdir):
        script = SCRIPTS / "simple_print.py"
        recording = os.path.join(tmpdir, "recording")
        rec = run_record(script, recording)
        assert rec.returncode == 0, f"stderr: {rec.stderr}"

    def test_replay_matches_record(self, tmpdir):
        rec, rep = record_and_replay(tmpdir, "simple_print.py")
        assert rep.returncode == 0, f"Replay stderr: {rep.stderr}"
        assert rec.stdout == rep.stdout


class TestFork:
    """Tests for os.fork — parent and child both traced."""

    @pytest.mark.skipif(
        not hasattr(os, "fork"), reason="os.fork not available"
    )
    def test_fork_record_succeeds(self, tmpdir):
        script = SCRIPTS / "fork_child.py"
        recording = os.path.join(tmpdir, "recording")
        rec = run_record(script, recording)
        assert rec.returncode == 0, f"stderr: {rec.stderr}"
        # Both parent and child should have printed
        assert "parent:" in rec.stdout
        assert "child:" in rec.stdout

    @pytest.mark.skipif(
        not hasattr(os, "fork"), reason="os.fork not available"
    )
    def test_fork_replay_matches(self, tmpdir):
        rec, rep = record_and_replay(tmpdir, "fork_child.py")
        assert rep.returncode == 0, f"Replay stderr: {rep.stderr}"
        assert rec.stdout == rep.stdout


class TestSubprocess:
    """Tests for subprocess.run — child launched via _posixsubprocess.fork_exec."""

    def test_subprocess_record_succeeds(self, tmpdir):
        script = SCRIPTS / "subprocess_echo.py"
        recording = os.path.join(tmpdir, "recording")
        rec = run_record(script, recording)
        assert rec.returncode == 0, f"stderr: {rec.stderr}"
        assert "parent got: hello from child" in rec.stdout

    def test_subprocess_replay_matches(self, tmpdir):
        rec, rep = record_and_replay(tmpdir, "subprocess_echo.py")
        assert rep.returncode == 0, f"Replay stderr: {rep.stderr}"
        assert rec.stdout == rep.stdout


class TestExec:
    """Tests for os.execv — process replaces itself."""

    @pytest.mark.skipif(
        not hasattr(os, "execv"), reason="os.execv not available"
    )
    def test_exec_record_succeeds(self, tmpdir):
        script = SCRIPTS / "exec_replacement.py"
        recording = os.path.join(tmpdir, "recording")
        rec = run_record(script, recording)
        assert rec.returncode == 0, f"stderr: {rec.stderr}"

    @pytest.mark.skipif(
        not hasattr(os, "execv"), reason="os.execv not available"
    )
    def test_exec_replay_matches(self, tmpdir):
        rec, rep = record_and_replay(tmpdir, "exec_replacement.py")
        assert rep.returncode == 0, f"Replay stderr: {rep.stderr}"
        assert rec.stdout == rep.stdout


class TestMultiProcess:
    """Tests for multiple subprocess invocations."""

    def test_multiple_subprocesses_record(self, tmpdir):
        script = SCRIPTS / "multiprocess_values.py"
        recording = os.path.join(tmpdir, "recording")
        rec = run_record(script, recording)
        assert rec.returncode == 0, f"stderr: {rec.stderr}"
        # Output should be a JSON list of 3 floats
        values = json.loads(rec.stdout.strip())
        assert len(values) == 3
        assert all(isinstance(v, float) for v in values)

    def test_multiple_subprocesses_replay_deterministic(self, tmpdir):
        rec, rep = record_and_replay(tmpdir, "multiprocess_values.py")
        assert rep.returncode == 0, f"Replay stderr: {rep.stderr}"
        assert rec.stdout == rep.stdout
