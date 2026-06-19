from __future__ import annotations

import json
import subprocess
from pathlib import Path

from retracesoftware import agent_diagnose, agent_inspect, agent_mcp, cli


def _inspect_report(recording: Path) -> dict:
    return {
        "recording": {"path": str(recording), "pid": None, "thread_id": "main"},
        "failure": {"reason": "exception"},
        "exception": {
            "type": "AssertionError",
            "message": "expected total to be 42",
            "assertion_text": "assert total == 42",
        },
        "application_stack": [
            {"filename": "/app/cart.py", "line": 17, "function": "checkout"},
        ],
        "locals": [
            {"name": "total", "type": "int", "repr": "41", "truncated": False},
            {"name": "items", "type": "list", "repr": "[...]", "truncated": True},
        ],
        "availability": {
            "cursor_available": True,
            "exception_available": True,
            "locals_available": True,
            "external_calls_available": False,
        },
        "external_calls": {},
        "control": {"returncode": 0, "responses": 4},
        "limitations": [],
    }


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


def test_diagnosis_plan_closes_agent_loop(tmp_path: Path) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")

    diagnosis = agent_diagnose.build_diagnosis(_inspect_report(recording), recording=str(recording))

    assert diagnosis["kind"] == "retrace_agent_diagnosis"
    assert diagnosis["status"] == "ready_for_agent_review"
    assert diagnosis["observations"]["exception"]["type"] == "AssertionError"
    assert diagnosis["hypotheses"][0]["id"] == "exception-state"
    assert diagnosis["next_actions"][0]["tool"] == "retrace_failures"
    assert diagnosis["next_actions"][1]["tool"] == "retrace_frame"
    assert diagnosis["next_actions"][2]["tool"] == "retrace_function_code"
    assert diagnosis["next_actions"][3]["tool"] == "retrace_eval"
    assert diagnosis["next_actions"][3]["arguments"]["expression"] == "total"
    assert diagnosis["next_actions"][4]["tool"] == "retrace_var"
    assert diagnosis["next_actions"][4]["arguments"]["name"] == "total"
    assert "Fetch retrace_function_code" in diagnosis["agent_loop"][2]
    assert "retrace_eval" in diagnosis["agent_loop"][3]
    assert "Accept a root cause only" in diagnosis["agent_loop"][-1]


def test_diagnose_cli_outputs_markdown(tmp_path: Path, monkeypatch, capsys) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")
    calls = []

    def fake_diagnose(recording_path, max_frames=5, max_vars=12, repr_budget=300):
        calls.append((recording_path, max_frames, max_vars, repr_budget))
        return agent_diagnose.build_diagnosis(_inspect_report(recording), recording=recording_path)

    monkeypatch.setattr(cli, "diagnose_recording", fake_diagnose)

    assert cli.main(["diagnose", "--recording", str(recording)]) == 0

    output = capsys.readouterr().out
    assert "# Retrace agent diagnosis" in output
    assert "retrace_frame" in output
    assert "Hypotheses:" in output
    assert calls == [(str(recording), 5, 12, 300)]


def test_function_code_extracts_selected_frame_function(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "cart.py"
    source.write_text(
        (
            "def helper():\n"
            "    return 1\n"
            "\n"
            "def checkout(items):\n"
            "    total = sum(items)\n"
            "    assert total == 42\n"
            "    return total\n"
        ),
        encoding="utf-8",
    )
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")
    stdout = "\n".join(
        [
            json.dumps({"id": "hello", "ok": True, "result": {}}),
            json.dumps({"kind": "stop", "payload": {"reason": "exception", "cursor": {"thread_id": 1}}}),
            json.dumps({"id": "inspect", "ok": True, "result": {}}),
            json.dumps(
                {
                    "id": "stack",
                    "ok": True,
                    "result": {
                        "frames": [
                            {
                                "filename": str(source),
                                "line": 6,
                                "function": "checkout",
                            }
                        ]
                    },
                }
            ),
        ]
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(agent_inspect, "_run_replay_control", fake_run)

    report = agent_inspect.inspect_function_code(str(recording), frame_index=0)

    assert report["function_code_available"] is True
    assert report["frame"]["function"] == "checkout"
    assert report["function_code"]["start_line"] == 4
    assert report["function_code"]["end_line"] == 7
    assert report["function_code"]["current_line"] == 6
    assert "def checkout(items):" in report["function_code"]["source"]
    assert "def helper" not in report["function_code"]["source"]


def test_function_code_cli_outputs_markdown(tmp_path: Path, monkeypatch, capsys) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")
    calls = []

    def fake_function_code(recording_path, frame_index=0, max_chars=12000):
        calls.append((recording_path, frame_index, max_chars))
        return {
            "recording": {"path": recording_path},
            "frame": {
                "index": frame_index,
                "available": True,
                "file": "/app/cart.py",
                "line": 6,
                "function": "checkout",
            },
            "function_code": {
                "available": True,
                "start_line": 4,
                "end_line": 7,
                "current_line": 6,
                "source_origin": "local_file",
                "truncated": False,
                "reason": None,
                "source": "def checkout(items):\n    return sum(items)\n",
            },
            "availability": {"source_available": True},
            "limitations": [],
        }

    monkeypatch.setattr(cli, "inspect_function_code", fake_function_code)

    assert cli.main(["function-code", "--recording", str(recording), "--frame", "0"]) == 0

    output = capsys.readouterr().out
    assert "# Retrace function code" in output
    assert "def checkout(items):" in output
    assert calls == [(str(recording), 0, 12000)]


def test_eval_expression_reports_value_preview(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "cart.py"
    source.write_text("def checkout():\n    total = 41\n    assert total == 42\n", encoding="utf-8")
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")
    stdout = "\n".join(
        [
            json.dumps({"id": "hello", "ok": True, "result": {}}),
            json.dumps({"kind": "stop", "payload": {"reason": "exception", "cursor": {"thread_id": 1}}}),
            json.dumps({"id": "inspect", "ok": True, "result": {}}),
            json.dumps(
                {
                    "id": "stack",
                    "ok": True,
                    "result": {
                        "frames": [
                            {
                                "filename": str(source),
                                "line": 3,
                                "function": "checkout",
                            }
                        ]
                    },
                }
            ),
            json.dumps({"id": "eval", "ok": True, "result": {"result": "41", "type": "int"}}),
        ]
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(agent_inspect, "_run_replay_control", fake_run)

    report = agent_inspect.inspect_expression(str(recording), frame_index=0, expression="total")

    assert report["evaluation_available"] is True
    assert report["frame"]["function"] == "checkout"
    assert report["evaluation"]["expression"] == "total"
    assert report["evaluation"]["value_preview"] == "41"
    assert report["evaluation"]["type"] == "int"


def test_eval_cli_outputs_markdown(tmp_path: Path, monkeypatch, capsys) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")
    calls = []

    def fake_eval(recording_path, frame_index=0, expression="", repr_budget=1200):
        calls.append((recording_path, frame_index, expression, repr_budget))
        return {
            "recording": {"path": recording_path},
            "frame": {
                "index": frame_index,
                "available": True,
                "file": "/app/cart.py",
                "line": 6,
                "function": "checkout",
            },
            "evaluation": {
                "available": True,
                "expression": expression,
                "value_preview": "41",
                "type": "int",
                "truncated": False,
                "reason": None,
            },
            "availability": {"evaluation_available": True},
            "limitations": [],
        }

    monkeypatch.setattr(cli, "inspect_expression", fake_eval)

    assert cli.main(["eval", "--recording", str(recording), "--expression", "total"]) == 0

    output = capsys.readouterr().out
    assert "# Retrace expression evaluation" in output
    assert "value_preview: 41" in output
    assert calls == [(str(recording), 0, "total", 1200)]


def test_failures_report_ranks_application_assertion(tmp_path: Path, monkeypatch) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")
    stdout = "\n".join(
        [
            json.dumps({"id": "hello", "ok": True, "result": {}}),
            json.dumps(
                {
                    "id": "failures",
                    "ok": True,
                    "result": {
                        "completed": True,
                        "reason": "eof",
                        "message_index": 10,
                        "candidates": [
                            {
                                "message_index": 1,
                                "cursor": {"thread_id": 0, "function_counts": [1], "f_lasti": 2},
                                "exception": {"type": "KeyError", "message": "noise", "control_flow": False},
                                "location": {"filename": "/lib/weakref.py", "line": 415, "function": "__getitem__"},
                                "classification": "stdlib",
                                "application_frame": False,
                            },
                            {
                                "message_index": 9,
                                "cursor": {"thread_id": 0, "function_counts": [2], "f_lasti": 8},
                                "exception": {
                                    "type": "AssertionError",
                                    "message": "expected 42",
                                    "assertion_text": "expected 42",
                                    "control_flow": False,
                                },
                                "location": {"filename": "/app/cart.py", "line": 17, "function": "checkout"},
                                "classification": "application",
                                "application_frame": True,
                            },
                        ],
                    },
                }
            ),
        ]
    )

    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=[], returncode=1, stdout=stdout, stderr="")

    monkeypatch.setattr(agent_inspect, "_run_replay_control", fake_run)

    report = agent_inspect.inspect_failures(str(recording))

    assert report["candidate_count"] == 2
    assert report["classification_counts"] == {"stdlib": 1, "application": 1}
    assert report["ranked_candidates"][0]["exception"]["type"] == "AssertionError"
    assert report["ranked_candidates"][0]["classification"] == "application"


def test_failures_cli_outputs_markdown(tmp_path: Path, monkeypatch, capsys) -> None:
    recording = tmp_path / "recording.bin"
    recording.write_bytes(b"recording")
    calls = []

    def fake_failures(recording_path, limit=5000):
        calls.append((recording_path, limit))
        return {
            "recording": {"path": recording_path},
            "candidate_count": 1,
            "classification_counts": {"application": 1},
            "completed": True,
            "reason": "eof",
            "ranked_candidates": [
                {
                    "rank": 1,
                    "score": 150,
                    "exception": {
                        "type": "AssertionError",
                        "message": "expected 42",
                        "control_flow": False,
                    },
                    "location": {"file": "/app/cart.py", "line": 17, "function": "checkout"},
                    "classification": "application",
                }
            ],
            "limitations": [],
        }

    monkeypatch.setattr(cli, "inspect_failures", fake_failures)

    assert cli.main(["failures", "--recording", str(recording)]) == 0

    output = capsys.readouterr().out
    assert "# Retrace failure candidates" in output
    assert "AssertionError" in output
    assert "classification_counts" in output
    assert calls == [(str(recording), 5000)]


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


def test_mcp_agent_workflow_guides_tool_order() -> None:
    tool_names = {tool["name"] for tool in agent_mcp.list_tools()}

    result = agent_mcp.call_tool("retrace_agent_workflow", {})
    payload = json.loads(result["content"][0]["text"])
    sequence = [step["tool"] for step in payload["default_sequence"]]

    assert "retrace_agent_workflow" in tool_names
    assert sequence[:4] == [
        "retrace_diagnose",
        "retrace_failures",
        "retrace_frame",
        "retrace_function_code",
    ]
    assert "Do not claim root cause" in payload["rules"][0]
    assert payload["root_cause_report_schema"]["confidence"] == "low|medium|high"


def test_mcp_diagnose_tool_uses_environment_fallback(monkeypatch) -> None:
    calls = []

    def fake_diagnose(recording, max_frames=5, max_vars=12, repr_budget=300):
        calls.append((recording, max_frames, max_vars, repr_budget))
        return {"kind": "retrace_agent_diagnosis", "recording": recording}

    monkeypatch.setenv("RETRACE_RECORDING", "/tmp/example.retrace")
    monkeypatch.setattr(agent_mcp, "retrace_diagnose", fake_diagnose)

    tool_names = {tool["name"] for tool in agent_mcp.list_tools()}
    result = agent_mcp.call_tool("retrace_diagnose", {})
    text = result["content"][0]["text"]

    assert "retrace_diagnose" in tool_names
    assert json.loads(text) == {
        "kind": "retrace_agent_diagnosis",
        "recording": "/tmp/example.retrace",
    }
    assert calls == [("/tmp/example.retrace", 5, 12, 300)]


def test_mcp_failures_tool_uses_environment_fallback(monkeypatch) -> None:
    calls = []

    def fake_failures(recording, limit=5000):
        calls.append((recording, limit))
        return {"candidate_count": 1}

    monkeypatch.setenv("RETRACE_RECORDING", "/tmp/example.retrace")
    monkeypatch.setattr(agent_mcp, "retrace_failures", fake_failures)

    tool_names = {tool["name"] for tool in agent_mcp.list_tools()}
    result = agent_mcp.call_tool("retrace_failures", {})
    text = result["content"][0]["text"]

    assert "retrace_failures" in tool_names
    assert json.loads(text) == {"candidate_count": 1}
    assert calls == [("/tmp/example.retrace", 5000)]


def test_mcp_function_code_tool_uses_environment_fallback(monkeypatch) -> None:
    calls = []

    def fake_function_code(recording, frame, max_chars=12000):
        calls.append((recording, frame, max_chars))
        return {"function_code": {"available": True}}

    monkeypatch.setenv("RETRACE_RECORDING", "/tmp/example.retrace")
    monkeypatch.setattr(agent_mcp, "retrace_function_code", fake_function_code)

    tool_names = {tool["name"] for tool in agent_mcp.list_tools()}
    result = agent_mcp.call_tool("retrace_function_code", {"frame": 2})
    text = result["content"][0]["text"]

    assert "retrace_function_code" in tool_names
    assert json.loads(text) == {"function_code": {"available": True}}
    assert calls == [("/tmp/example.retrace", 2, 12000)]


def test_mcp_eval_tool_uses_environment_fallback(monkeypatch) -> None:
    calls = []

    def fake_eval(recording, frame, expression, repr_budget=1200):
        calls.append((recording, frame, expression, repr_budget))
        return {"evaluation": {"available": True, "value_preview": "41"}}

    monkeypatch.setenv("RETRACE_RECORDING", "/tmp/example.retrace")
    monkeypatch.setattr(agent_mcp, "retrace_eval", fake_eval)

    tool_names = {tool["name"] for tool in agent_mcp.list_tools()}
    result = agent_mcp.call_tool("retrace_eval", {"frame": 2, "expression": "total"})
    text = result["content"][0]["text"]

    assert "retrace_eval" in tool_names
    assert json.loads(text) == {"evaluation": {"available": True, "value_preview": "41"}}
    assert calls == [("/tmp/example.retrace", 2, "total", 1200)]


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
