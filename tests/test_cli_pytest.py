import json
import subprocess
import sys

from retracesoftware import cli
from retracesoftware.pytest_runs import (
    PLACEHOLDER_RECORDING_TEXT,
    build_failed_test_manifest,
    write_manifest,
)


def test_pytest_wrapper_builds_record_command(monkeypatch, tmp_path):
    calls = []

    def fake_run(command):
        calls.append(command)
        return subprocess.CompletedProcess(command, 1)

    recording = tmp_path / "failure.retrace"
    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    exit_code = cli.main([
        "pytest",
        "--recording",
        str(recording),
        "--",
        "examples/ci_failure_demo",
        "-q",
    ])

    assert exit_code == 1
    assert calls == [[
        sys.executable,
        "-m",
        "retracesoftware",
        "--recording",
        str(recording),
        "--format",
        "binary",
        "--stacktraces",
        "--",
        "-m",
        "pytest",
        "examples/ci_failure_demo",
        "-q",
    ]]


def test_pytest_wrapper_discards_passing_recording(monkeypatch, tmp_path):
    recording = tmp_path / "passing.retrace"
    recording.write_bytes(b"trace")

    def fake_run(command):
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    exit_code = cli.main(["pytest", "--recording", str(recording)])

    assert exit_code == 0
    assert not recording.exists()


def test_pytest_wrapper_accepts_pytest_args_without_separator(monkeypatch):
    calls = []

    def fake_run(command):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    exit_code = cli.main(["pytest", "examples/ci_failure_demo", "-q"])

    assert exit_code == 0
    assert calls[0][-3:] == ["pytest", "examples/ci_failure_demo", "-q"]


def test_pytest_wrapper_can_keep_passing_recording(monkeypatch, tmp_path):
    recording = tmp_path / "passing.retrace"
    recording.write_bytes(b"trace")

    def fake_run(command):
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)

    exit_code = cli.main([
        "pytest",
        "--recording",
        str(recording),
        "--keep-passing",
    ])

    assert exit_code == 0
    assert recording.exists()


def test_runs_command_lists_failed_test_manifests(monkeypatch, tmp_path, capsys):
    runs_dir = tmp_path / ".retrace" / "runs"
    write_manifest(
        build_failed_test_manifest(
            run_id="older",
            created_at="2026-06-05T09:00:00Z",
            node_id="tests/test_example.py::test_older",
            exception_type="AssertionError",
            exception_message="older failed",
        ),
        runs_dir=runs_dir,
    )
    write_manifest(
        build_failed_test_manifest(
            run_id="newer",
            created_at="2026-06-05T11:00:00Z",
            node_id="tests/test_example.py::test_newer",
            exception_type="ValueError",
            exception_message="newer failed",
        ),
        runs_dir=runs_dir,
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["runs"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert output.index("test_newer") < output.index("test_older")
    assert "ValueError: newer failed" in output


def test_inspect_latest_uses_latest_failed_test_recording(monkeypatch, tmp_path, capsys):
    recording = tmp_path / "latest.bin"
    recording.write_bytes(b"placeholder")
    write_manifest(
        build_failed_test_manifest(
            run_id="latest",
            created_at="2026-06-05T11:00:00Z",
            recording_path=recording,
            node_id="tests/test_example.py::test_latest",
            exception_type="AssertionError",
            exception_message="latest failed",
            recording_placeholder=False,
            recording_available=True,
        ),
        runs_dir=tmp_path / ".retrace" / "runs",
    )
    monkeypatch.chdir(tmp_path)

    calls = []

    def fake_inspect_recording(recording_path, **kwargs):
        calls.append((recording_path, kwargs))
        return {"control": {"responses": [{"ok": True}]}}

    monkeypatch.setattr(cli, "inspect_recording", fake_inspect_recording)

    exit_code = cli.main(["inspect", "--latest", "--json"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert calls == [(
        str(recording),
        {
            "pid": None,
            "max_frames": 5,
            "max_vars": 50,
            "repr_budget": 300,
        },
    )]
    assert json.loads(output)["control"]["responses"][0]["ok"] is True


def test_agent_context_latest_no_run_fails_helpfully(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["agent-context", "--latest"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "No Retrace failed-test runs found" in captured.err


def test_mcp_latest_placeholder_recording_fails_clearly(monkeypatch, tmp_path, capsys):
    recording = tmp_path / ".retrace" / "runs" / "latest" / "recording.bin"
    recording.parent.mkdir(parents=True)
    recording.write_text(PLACEHOLDER_RECORDING_TEXT, encoding="utf-8")
    write_manifest(
        build_failed_test_manifest(
            run_id="latest",
            created_at="2026-06-05T11:00:00Z",
            recording_path=recording,
            node_id="tests/test_example.py::test_latest",
            recording_available=True,
        ),
        runs_dir=tmp_path / ".retrace" / "runs",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["mcp", "--latest"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "recording is a placeholder" in captured.err


def test_mcp_latest_unavailable_recording_fails_clearly(monkeypatch, tmp_path, capsys):
    recording = tmp_path / ".retrace" / "runs" / "latest" / "recording.bin"
    write_manifest(
        build_failed_test_manifest(
            run_id="latest",
            created_at="2026-06-05T11:00:00Z",
            recording_path=recording,
            node_id="tests/test_example.py::test_latest",
            recording_placeholder=False,
            recording_capture_method="full-session-clean-subprocess",
            recording_capture_scope="full_session",
            recording_failure_selection="first_failure",
            recording_available=False,
            recording_failure_reason="pytest exited before a replayable recording was available",
        ),
        runs_dir=tmp_path / ".retrace" / "runs",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["mcp", "--latest"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "recording is unavailable" in captured.err
    assert "pytest exited before a replayable recording was available" in captured.err


def test_inspect_latest_unavailable_recording_fails_before_replay(monkeypatch, tmp_path, capsys):
    recording = tmp_path / ".retrace" / "runs" / "latest" / "recording.bin"
    write_manifest(
        build_failed_test_manifest(
            run_id="latest",
            created_at="2026-06-05T11:00:00Z",
            recording_path=recording,
            node_id="tests/test_example.py::test_latest",
            recording_placeholder=False,
            recording_capture_method="full-session-clean-subprocess",
            recording_capture_scope="full_session",
            recording_failure_selection="first_failure",
            recording_available=False,
            recording_failure_reason="recording command could not start",
        ),
        runs_dir=tmp_path / ".retrace" / "runs",
    )
    monkeypatch.chdir(tmp_path)

    calls = []
    monkeypatch.setattr(cli, "inspect_recording", lambda *args, **kwargs: calls.append(args))

    exit_code = cli.main(["inspect", "--latest"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "latest recording is unavailable" in captured.err
    assert "recording command could not start" in captured.err
    assert calls == []


def test_inspect_latest_missing_recording_fails_clearly(monkeypatch, tmp_path, capsys):
    recording = tmp_path / "missing.bin"
    write_manifest(
        build_failed_test_manifest(
            run_id="latest",
            created_at="2026-06-05T11:00:00Z",
            recording_path=recording,
            node_id="tests/test_example.py::test_latest",
            recording_placeholder=False,
            recording_available=True,
        ),
        runs_dir=tmp_path / ".retrace" / "runs",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["inspect", "--latest"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "latest recording is missing" in captured.err
    assert str(recording) in captured.err


def test_inspect_latest_no_inspectable_state_is_actionable(monkeypatch, tmp_path, capsys):
    recording = tmp_path / "latest.bin"
    recording.write_bytes(b"real retrace recording")
    write_manifest(
        build_failed_test_manifest(
            run_id="latest",
            created_at="2026-06-05T11:00:00Z",
            recording_path=recording,
            node_id="tests/test_example.py::test_latest",
            recording_placeholder=False,
            recording_available=True,
        ),
        runs_dir=tmp_path / ".retrace" / "runs",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "inspect_recording", lambda *args, **kwargs: {"control": {"responses": []}})

    exit_code = cli.main(["inspect", "--latest", "--json"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "replay/control returned no inspectable state" in captured.err
    assert json.loads(captured.out)["control"]["responses"] == []


def test_mcp_latest_no_run_fails_helpfully(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["mcp", "--latest"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "No Retrace failed-test runs found" in captured.err


def test_mcp_latest_real_recording_launches_existing_mcp_path(monkeypatch, tmp_path):
    recording = tmp_path / "latest.bin"
    recording.write_bytes(b"real retrace recording")
    write_manifest(
        build_failed_test_manifest(
            run_id="latest",
            created_at="2026-06-05T11:00:00Z",
            recording_path=recording,
            node_id="tests/test_example.py::test_latest",
            recording_placeholder=False,
            recording_capture_method="full-session-clean-subprocess",
            recording_capture_scope="full_session",
            recording_failure_selection="first_failure",
            recording_available=True,
        ),
        runs_dir=tmp_path / ".retrace" / "runs",
    )
    monkeypatch.chdir(tmp_path)
    calls = []

    def fake_launch_mcp(*, recording=None, manifest=None):
        calls.append((recording, manifest))
        return 0

    monkeypatch.setattr(cli, "_launch_mcp", fake_launch_mcp)

    exit_code = cli.main(["mcp", "--latest"])

    assert exit_code == 0
    assert calls[0][0] == recording
    assert calls[0][1]["run_id"] == "latest"


def test_clean_all_yes_deletes_runs(monkeypatch, tmp_path, capsys):
    runs_dir = tmp_path / ".retrace" / "runs"
    write_manifest(build_failed_test_manifest(run_id="one", created_at="2026-06-05T10:00:00Z"), runs_dir=runs_dir)
    write_manifest(build_failed_test_manifest(run_id="two", created_at="2026-06-05T11:00:00Z"), runs_dir=runs_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["clean", "--all", "--yes"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "Deleted 2 Retrace failed-test run" in output
    assert not (runs_dir / "one").exists()
    assert not (runs_dir / "two").exists()


def test_clean_older_than_yes_deletes_only_old_runs(monkeypatch, tmp_path):
    runs_dir = tmp_path / ".retrace" / "runs"
    write_manifest(build_failed_test_manifest(run_id="old", created_at="2000-01-01T00:00:00Z"), runs_dir=runs_dir)
    write_manifest(build_failed_test_manifest(run_id="new", created_at="2999-01-01T00:00:00Z"), runs_dir=runs_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["clean", "--older-than", "7d", "--yes"])

    assert exit_code == 0
    assert not (runs_dir / "old").exists()
    assert (runs_dir / "new").exists()


def test_clean_latest_yes_deletes_only_latest_run(monkeypatch, tmp_path):
    runs_dir = tmp_path / ".retrace" / "runs"
    write_manifest(build_failed_test_manifest(run_id="old", created_at="2026-06-05T10:00:00Z"), runs_dir=runs_dir)
    write_manifest(build_failed_test_manifest(run_id="latest", created_at="2026-06-05T11:00:00Z"), runs_dir=runs_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["clean", "--latest", "--yes"])

    assert exit_code == 0
    assert (runs_dir / "old").exists()
    assert not (runs_dir / "latest").exists()


def test_clean_no_runs_is_helpful(monkeypatch, tmp_path, capsys):
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["clean", "--all", "--yes"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "No Retrace failed-test runs found" in output


def test_clean_does_not_delete_unrelated_files_outside_runs(monkeypatch, tmp_path):
    runs_dir = tmp_path / ".retrace" / "runs"
    outside = tmp_path / "keep.txt"
    outside.write_text("do not delete", encoding="utf-8")
    write_manifest(build_failed_test_manifest(run_id="run", created_at="2026-06-05T10:00:00Z"), runs_dir=runs_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["clean", "--all", "--yes"])

    assert exit_code == 0
    assert outside.read_text(encoding="utf-8") == "do not delete"
