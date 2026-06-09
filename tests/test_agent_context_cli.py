from __future__ import annotations

import json
from pathlib import Path

from retracesoftware import agent_mcp, cli


def test_agent_context_text_for_existing_recording(tmp_path: Path, capsys) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")

    assert cli.main(["agent-context", "--recording", str(recording)]) == 0

    output = capsys.readouterr().out
    assert "Retrace recording context" in output
    assert f"path: {recording}" in output
    assert "exists: yes" in output
    assert "retrace inspect --recording" in output
    assert "retrace mcp --recording" in output
    assert "Recordings may contain runtime data" in output


def test_agent_context_json_for_existing_recording(tmp_path: Path, capsys) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")

    assert cli.main(["agent-context", "--recording", str(recording), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["recording"]["path"] == str(recording)
    assert payload["recording"]["exists"] is True
    assert payload["recording"]["size_bytes"] == len(b"recording")
    assert payload["inspection"]["frames_available"] is False
    assert payload["inspection"]["locals_available"] is False
    assert payload["safety"]["may_contain_runtime_data"] is True


def test_agent_context_missing_recording_is_clear(tmp_path: Path, capsys) -> None:
    recording = tmp_path / "missing.bin"

    assert cli.main(["agent-context", "--recording", str(recording)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "recording does not exist" in captured.err
    assert str(recording) in captured.err


def test_agent_context_invalid_manifest_is_clear(tmp_path: Path, capsys) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{not json", encoding="utf-8")

    assert (
        cli.main(
            [
                "agent-context",
                "--recording",
                str(recording),
                "--manifest",
                str(manifest),
            ]
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "retrace agent-context failed" in captured.err
    assert "is not valid JSON" in captured.err
    assert str(manifest) in captured.err
    assert "Traceback" not in captured.err


def test_latest_recording_pointer_resolves_from_nested_directory(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    recording = tmp_path / "recordings" / "recording with spaces.bin"
    recording.parent.mkdir()
    recording.write_bytes(b"recording")
    retrace_dir = tmp_path / ".retrace"
    retrace_dir.mkdir()
    (retrace_dir / "latest-recording.json").write_text(
        json.dumps({"recording_path": "recordings/recording with spaces.bin"}),
        encoding="utf-8",
    )
    nested = tmp_path / "pkg" / "tests"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    assert cli.main(["agent-context", "--latest", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["recording"]["path"] == str(recording)
    assert payload["commands"]["inspect"] == f"retrace inspect --recording '{recording}'"


def test_mcp_missing_recording_is_clear(tmp_path: Path, capsys) -> None:
    recording = tmp_path / "missing.bin"

    assert cli.main(["mcp", "--recording", str(recording)]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "recording does not exist" in captured.err
    assert str(recording) in captured.err


def test_mcp_existing_recording_prepares_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")
    calls = []

    def fake_launch(*, recording=None, manifest=None):
        calls.append((recording, manifest))
        return 0

    monkeypatch.setattr(cli, "_launch_mcp_server", fake_launch)

    assert cli.main(["mcp", "--recording", str(recording)]) == 0
    assert calls == [(recording, None)]


def test_mcp_tools_use_recording_environment_fallback(monkeypatch) -> None:
    calls = []

    def fake_inspect(recording, max_frames=5, max_vars=50, repr_budget=300):
        calls.append((recording, max_frames, max_vars, repr_budget))
        return {"recording": recording}

    monkeypatch.setenv("RETRACE_RECORDING", "/tmp/example.retrace")
    monkeypatch.setattr(agent_mcp, "retrace_inspect", fake_inspect)

    result = agent_mcp.call_tool("retrace_inspect", {})
    text = result["content"][0]["text"]

    assert json.loads(text) == {"recording": "/tmp/example.retrace"}
    assert calls == [("/tmp/example.retrace", 5, 50, 300)]


def test_inspect_degraded_output_is_clear(tmp_path: Path, monkeypatch, capsys) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")

    def fake_inspect(*args, **kwargs):
        return {
            "recording": {"path": str(recording)},
            "failure": {},
            "exception": {},
            "application_stack": [],
            "locals": [],
            "external_calls": {},
            "availability": {
                "cursor_available": False,
                "exception_available": False,
                "locals_available": False,
                "external_calls_available": False,
            },
            "control": {"responses": 0},
            "limitations": [],
        }

    monkeypatch.setattr(cli, "inspect_recording", fake_inspect)

    assert cli.main(["inspect", "--recording", str(recording)]) == 1

    captured = capsys.readouterr()
    assert "Recording found, but no inspectable state was available" in captured.err
    assert "retrace mcp --recording" in captured.err
