"""Whole-process tests for fork and exec under retrace.

Each test records a script via ``python -m retracesoftware``, then
replays it, and asserts that:

  1. Record succeeds (exit code 0).
  2. Replay succeeds (exit code 0).
  3. Replay stdout matches record stdout — proving deterministic replay.

"""
import os
import sys
import json
from pathlib import Path

import pytest

from conftest import run_record, run_replay


SCRIPTS = Path(__file__).parent / "scripts"


# ── helpers ────────────────────────────────────────────────────────

def record_and_replay(tmpdir, script_name, extra_record_args=None):
    """Record a script, then replay it.  Return (record_proc, replay_proc)."""
    script = SCRIPTS / script_name
    recording = os.path.join(tmpdir, "trace.bin")

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
        recording = os.path.join(tmpdir, "trace.bin")
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
        recording = os.path.join(tmpdir, "trace.bin")
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
        recording = os.path.join(tmpdir, "trace.bin")
        rec = run_record(script, recording)
        assert rec.returncode == 0, f"stderr: {rec.stderr}"
        assert "parent got: hello from child" in rec.stdout

    @pytest.mark.xfail(
        reason="Replay of subprocess.Popen with capture_output fails: "
               "replayed pipe() FDs are not real file descriptors"
    )
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
        recording = os.path.join(tmpdir, "trace.bin")
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
        recording = os.path.join(tmpdir, "trace.bin")
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


# ── fork tree: record all paths, replay specific paths ─────────

class TestForkTree:
    """Record a script that forks 3 times (8 leaf processes), then
    replay with --fork_path to follow a single path through the tree."""

    @pytest.mark.skipif(
        not hasattr(os, "fork"), reason="os.fork not available"
    )
    def test_record_produces_all_paths(self, tmpdir):
        script = SCRIPTS / "fork_tree.py"
        recording = os.path.join(tmpdir, "trace.bin")
        rec = run_record(script, recording)
        assert rec.returncode == 0, f"stderr: {rec.stderr}"

        paths = sorted(
            line.split(":")[1]
            for line in rec.stdout.strip().splitlines()
            if line.startswith("path:")
        )
        expected = sorted(format(i, '03b') for i in range(8))
        assert paths == expected

    @pytest.mark.skipif(
        not hasattr(os, "fork"), reason="os.fork not available"
    )
    @pytest.mark.parametrize("fork_path", [
        "000", "001", "010", "011", "100", "101", "110", "111",
    ])
    def test_replay_follows_fork_path(self, tmpdir, fork_path):
        script = SCRIPTS / "fork_tree.py"
        recording = os.path.join(tmpdir, "trace.bin")
        rec = run_record(script, recording)
        assert rec.returncode == 0, f"stderr: {rec.stderr}"

        rep = run_replay(recording, extra_args=["--fork_path", fork_path])
        assert rep.returncode == 0, (
            f"Replay failed (exit {rep.returncode}) for --fork_path {fork_path}:\n"
            f"stdout: {rep.stdout}\nstderr: {rep.stderr}"
        )

        lines = [
            line for line in rep.stdout.strip().splitlines()
            if line.startswith("path:")
        ]
        assert len(lines) == 1, (
            f"Expected exactly 1 path line, got {len(lines)}: {lines}"
        )
        assert lines[0] == f"path:{fork_path}"


# ── subprocess inherits recording ───────────────────────────────

class TestSubprocessRecorded:
    """Verify child subprocesses are recorded via RETRACE_RECORDING inheritance.

    A sitecustomize.py is injected via PYTHONPATH so that every child
    Python process loads autoenable, which sees RETRACE_RECORDING and
    records to the same trace file.

    The child prints time.time() which is non-deterministic.  If the child
    is recorded, replay will reproduce the exact same value.  If it isn't,
    the values will differ.
    """

    @pytest.fixture
    def autoenable_env(self, tmpdir):
        """Create a temp dir with sitecustomize.py that triggers autoenable."""
        site_dir = os.path.join(tmpdir, "site")
        os.makedirs(site_dir)
        with open(os.path.join(site_dir, "sitecustomize.py"), "w") as f:
            f.write("import retracesoftware.autoenable\n")

        pythonpath = os.environ.get("PYTHONPATH", "")
        new_pythonpath = f"{site_dir}:{pythonpath}" if pythonpath else site_dir
        return {"PYTHONPATH": new_pythonpath}

    def test_subprocess_time_record_succeeds(self, tmpdir, autoenable_env):
        script = SCRIPTS / "subprocess_time.py"
        recording = os.path.join(tmpdir, "trace.bin")

        env = {**autoenable_env, "RETRACE_RECORDING": recording}

        rec = run_record(script, recording, env=env)
        assert rec.returncode == 0, (
            f"Record failed (exit {rec.returncode}):\n"
            f"stdout: {rec.stdout}\nstderr: {rec.stderr}"
        )
        assert "child_time:" in rec.stdout

    @pytest.mark.xfail(
        reason="Replay of subprocess.Popen with capture_output fails: "
               "replayed pipe() FDs are not real file descriptors"
    )
    def test_subprocess_time_replay_deterministic(self, tmpdir, autoenable_env):
        script = SCRIPTS / "subprocess_time.py"
        recording = os.path.join(tmpdir, "trace.bin")

        env = {**autoenable_env, "RETRACE_RECORDING": recording}

        rec = run_record(script, recording, env=env)
        assert rec.returncode == 0

        rep = run_replay(recording)
        assert rep.returncode == 0, f"Replay stderr: {rep.stderr}"
        assert rec.stdout == rep.stdout, (
            f"Child subprocess was not recorded — time differs:\n"
            f"  record: {rec.stdout!r}\n  replay: {rep.stdout!r}"
        )


# ── parse_fork_path unit tests ─────────────────────────────────

from retracesoftware.__main__ import parse_fork_path


class TestForkPath:
    """Unit tests for the parse_fork_path helper."""

    def test_empty_string(self):
        assert parse_fork_path('') == ''

    def test_none_like(self):
        assert parse_fork_path(None) == ''

    def test_child_keyword(self):
        result = parse_fork_path('child')
        assert result == '1' * 1000

    def test_parent_keyword(self):
        assert parse_fork_path('parent') == ''

    def test_binary_passthrough(self):
        assert parse_fork_path('1101') == '1101'

    def test_binary_all_zeros(self):
        assert parse_fork_path('0000') == '0000'

    def test_rle_child_start(self):
        assert parse_fork_path('child-2-1-1') == '1101'

    def test_rle_parent_start(self):
        assert parse_fork_path('parent-3-2') == '00011'

    def test_rle_single_run(self):
        assert parse_fork_path('child-5') == '11111'
