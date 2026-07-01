import subprocess
import sys
from types import SimpleNamespace

import pytest

from retracesoftware.ai_driver import (
    AVAILABLE_TOOLS,
    DAPExecutor,
    DAPSession,
    MAX_SOURCE_CONTEXT_LINE_CHARS,
    _executor_session_state,
    _initial_observation,
    _prime_pytest_failure_breakpoint,
    _pytest_failure_hint_from_output,
    _pytest_failure_hint,
    _select_pytest_failure_candidate,
    _source_window,
)


def test_select_pytest_failure_candidate_skips_site_packages():
    report = {
        "ranked_candidates": [
            {
                "rank": 1,
                "score": 80,
                "classification": "site_packages",
                "exception": {"type": "AssertionError", "message": ""},
                "location": {
                    "filename": "/venv/lib/python3.11/site-packages/_pytest/config/__init__.py",
                    "line": 2023,
                    "function": "_assertion_supported",
                },
            },
            {
                "rank": 2,
                "score": 150,
                "classification": "application",
                "exception": {"type": "AssertionError", "message": "real failure"},
                "location": {"file": "/tmp/tests/test_financial_report.py", "line": 42, "function": "test_generate_financial_report"},
            },
        ],
    }

    hint = _select_pytest_failure_candidate(report)

    assert hint is not None
    assert hint["classification"] == "application"
    assert hint["function"] == "test_generate_financial_report"


def test_select_pytest_failure_candidate_returns_none_without_application_candidate():
    report = {
        "ranked_candidates": [
            {
                "rank": 1,
                "score": 80,
                "classification": "site_packages",
                "exception": {"type": "AssertionError", "message": ""},
                "location": {
                    "filename": "/venv/lib/python3.11/site-packages/_pytest/config/__init__.py",
                    "line": 2023,
                    "function": "_assertion_supported",
                },
            },
        ],
    }

    assert _select_pytest_failure_candidate(report) is None


def test_pytest_failure_hint_returns_none_when_only_site_packages_candidate(monkeypatch, tmp_path):
    recording = tmp_path / "pid.bin"
    recording.write_bytes(b"fake")

    monkeypatch.setattr("retracesoftware.ai_driver._control_recording_for_trace", lambda trace: recording)
    monkeypatch.setattr("retracesoftware.ai_driver._recording_cwd_for_trace", lambda trace: tmp_path)
    monkeypatch.setattr("retracesoftware.ai_driver.replay_binary_path", lambda: "/fake/replay")
    monkeypatch.setattr(
        "retracesoftware.ai_driver.subprocess.run",
        lambda *a, **kw: SimpleNamespace(stdout="", stderr="", returncode=1),
    )
    monkeypatch.setattr(
        "retracesoftware.agent_inspect.inspect_failures",
        lambda *a, **kw: {
            "ranked_candidates": [
                {
                    "rank": 1,
                    "score": 80,
                    "classification": "site_packages",
                    "exception": {"type": "AssertionError", "message": ""},
                    "location": {
                        "filename": str(tmp_path / ".venv/lib/python3.11/site-packages/_pytest/config/__init__.py"),
                        "line": 2023,
                        "function": "_assertion_supported",
                    },
                },
            ],
        },
    )

    assert _pytest_failure_hint(str(tmp_path / "case.retrace")) is None


def test_initial_observation_skips_priming_without_application_hint(monkeypatch):
    executor = object.__new__(DAPExecutor)
    monkeypatch.setattr("retracesoftware.ai_driver._pytest_failure_hint", lambda trace: None)
    monkeypatch.setattr(
        "retracesoftware.ai_driver._prime_pytest_failure_breakpoint",
        lambda executor, hint: pytest.fail("should not prime without application hint"),
    )

    transcript = []
    observation = _initial_observation(
        SimpleNamespace(task="Target command: -m pytest tests.", trace="/tmp/trace.retrace"),
        executor,
        transcript,
    )

    assert transcript == []
    assert "No application failure candidate" in observation["summary"]
    assert "tool_result" not in observation


def test_select_pytest_failure_candidate_prefers_application_assertion():
    report = {
        "ranked_candidates": [
            {
                "rank": 1,
                "score": 100,
                "classification": "application",
                "exception": {"type": "ModuleNotFoundError", "message": "handled"},
                "location": {"filename": "/tmp/test_case.py", "line": 2, "function": "<module>"},
            },
            {
                "rank": 2,
                "score": 150,
                "classification": "application",
                "exception": {"type": "AssertionError", "message": "real failure"},
                "location": {"file": "/tmp/test_case.py", "line": 7, "function": "test_case"},
            },
        ],
    }

    hint = _select_pytest_failure_candidate(report)

    assert hint == {
        "filename": "/tmp/test_case.py",
        "line": 7,
        "function": "test_case",
        "exception_type": "AssertionError",
        "exception_message": "real failure",
        "classification": "application",
        "rank": 2,
        "score": 150,
    }


def test_source_window_omits_empty_lines_for_service_payload():
    context = _source_window("first\n\n    \nlast\n", line=2, before=2, after=2)

    assert context == [
        {"line": 1, "text": "first", "current": False},
        {"line": 4, "text": "last", "current": False},
    ]


def test_source_window_truncates_long_lines_for_hosted_service_contract():
    long_line = "x" * (MAX_SOURCE_CONTEXT_LINE_CHARS + 200)

    context = _source_window(long_line + "\n", line=1, before=0, after=0)

    assert len(context) == 1
    assert context[0]["current"] is True
    assert context[0]["text"].endswith(" ... <truncated>")
    assert len(context[0]["text"]) == MAX_SOURCE_CONTEXT_LINE_CHARS


def test_available_tools_match_hosted_service_contract():
    assert "set_exception_breakpoints" not in AVAILABLE_TOOLS


def test_dap_session_does_not_report_evaluate_unavailable_when_tool_is_exposed():
    session = object.__new__(DAPSession)
    session.capabilities = {
        "supportsConditionalBreakpoints": True,
        "supportsStepBack": True,
    }

    assert "evaluate_expression" in AVAILABLE_TOOLS
    assert session._capability_state()["evaluate"] != "unavailable"
    assert session._capability_state()["evaluate"] is True


def test_initial_observation_reports_prepositioned_pytest_session(monkeypatch):
    executor = object.__new__(DAPExecutor)
    executor.session = SimpleNamespace(
        state={"state": "stopped", "last_stop": {"reason": "breakpoint"}},
    )
    hint = {
        "filename": "/tmp/test_case.py",
        "line": 7,
        "function": "test_case",
        "exception_type": "AssertionError",
    }
    prelude = [
        {
            "tool": "get_stack_trace",
            "arguments": {},
            "result": {
                "ok": True,
                "summary": "DAP stack trace returned 1 frame.",
                "data": {
                    "stack_frames": [
                        {
                            "name": "test_case",
                            "source": {"path": "/tmp/test_case.py"},
                            "line": 7,
                        }
                    ],
                },
                "session": executor.session.state,
            },
        }
    ]
    monkeypatch.setattr("retracesoftware.ai_driver._pytest_failure_hint", lambda trace: hint)
    monkeypatch.setattr("retracesoftware.ai_driver._prime_pytest_failure_breakpoint", lambda e, h: prelude)

    transcript = []
    observation = _initial_observation(
        SimpleNamespace(task="Target command: -m pytest tests.", trace="/tmp/trace.retrace"),
        executor,
        transcript,
    )

    assert set(observation) == {"summary", "tool_result"}
    assert "pre-positioned DAP replay" in observation["summary"]
    assert observation["tool_result"]["pytest_failure_candidate"] == hint
    assert observation["tool_result"]["prelude"]["session"]["last_stop"]["reason"] == "breakpoint"
    assert transcript == prelude
    assert _executor_session_state(executor)["last_stop"]["reason"] == "breakpoint"


def test_pytest_failure_hint_prefers_bare_traceback_location_over_failed_node(tmp_path):
    test_file = tmp_path / "tests" / "test_financial_report.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "\n".join(
            [
                "def test_generate_financial_report():",
                "    expected = 59463.2",
                "    actual = 63750.41",
                "    assert actual == expected",
                "",
            ]
        ),
        encoding="utf-8",
    )
    output = """
tests/test_financial_report.py::test_generate_financial_report FAILED [100%]
=================================== FAILURES ===================================
________________________ test_generate_financial_report ________________________

    def test_generate_financial_report():
        expected = 59463.2
        actual = 63750.41
>       assert actual == expected

tests/test_financial_report.py:4:
E   AssertionError: assert 63750.41 == 59463.2
=========================== short test summary info ============================
FAILED tests/test_financial_report.py::test_generate_financial_report - AssertionError: assert 63750.41 == 59463.2
"""
    hint = _pytest_failure_hint_from_output(output, cwd=tmp_path)

    assert hint is not None
    assert hint["filename"] == str(test_file.resolve())
    assert hint["line"] == 4
    assert hint["function"] == "test_generate_financial_report"
    assert hint["exception_type"] == "AssertionError"
    assert "63750.41" in hint["exception_message"]


def test_pytest_failure_hint_from_dataframe_style_output_without_file_line(tmp_path):
    test_file = tmp_path / "tests" / "test_financial_report.py"
    test_file.parent.mkdir(parents=True)
    test_file.write_text(
        "\n".join(
            [
                "import pandas as pd",
                "",
                "def test_generate_financial_report():",
                "    expected = 1",
                "    actual = 2",
                "    assert expected == actual",
                "",
            ]
        ),
        encoding="utf-8",
    )
    output = """
tests/test_financial_report.py::test_generate_financial_report FAILED    [100%]
=================================== FAILURES ===================================
E   AssertionError: DataFrame.iloc[:, 3] (column name="amount_gbp") are different
E   At positional index 249, first diff: 63750.41 != 59463.2
=========================== short test summary info ============================
FAILED tests/test_financial_report.py::test_generate_financial_report - AssertionError
1 failed in 1.2s
"""
    hint = _pytest_failure_hint_from_output(output, cwd=tmp_path)

    assert hint is not None
    assert hint["filename"] == str(test_file.resolve())
    assert hint["line"] == 3
    assert hint["function"] == "test_generate_financial_report"
    assert hint["exception_type"] == "AssertionError"
    assert "63750.41" in hint["exception_message"]
    assert hint["classification"] == "application"


def test_pytest_failure_hint_from_replay_output_resolves_relative_pytest_frame(tmp_path):
    output = """
.F                                                                       [100%]
=================================== FAILURES ===================================
___________________________ test_partner_shape_fails ___________________________
tests/test_partner_shape.py:7: in test_partner_shape_fails
    assert actual["rows"] == actual["expected"]
E   AssertionError: assert 41 == 42
=========================== short test summary info ============================
FAILED tests/test_partner_shape.py::test_partner_shape_fails - AssertionError
1 failed, 1 passed in 0.05s
"""

    hint = _pytest_failure_hint_from_output(output, cwd=tmp_path)

    assert hint == {
        "filename": str((tmp_path / "tests/test_partner_shape.py").resolve()),
        "line": 7,
        "function": "test_partner_shape_fails",
        "exception_type": "AssertionError",
        "exception_message": "assert 41 == 42",
        "classification": "application",
        "rank": None,
        "score": 0,
    }


def test_pytest_failure_hint_falls_back_to_replayed_pytest_output(monkeypatch, tmp_path):
    recording = tmp_path / "pid.bin"
    recording.write_bytes(b"fake")
    output = """
.F                                                                       [100%]
=================================== FAILURES ===================================
___________________________ test_partner_shape_fails ___________________________
tests/test_partner_shape.py:7: in test_partner_shape_fails
    assert actual["rows"] == actual["expected"]
E   assert 41 == 42
=========================== short test summary info ============================
FAILED tests/test_partner_shape.py::test_partner_shape_fails - assert 41 == 42
1 failed, 1 passed in 0.05s
"""

    monkeypatch.setattr("retracesoftware.ai_driver._control_recording_for_trace", lambda trace: recording)
    monkeypatch.setattr("retracesoftware.ai_driver._recording_cwd_for_trace", lambda trace: tmp_path)
    monkeypatch.setattr("retracesoftware.agent_inspect.inspect_failures", lambda *a, **kw: {"ranked_candidates": []})
    monkeypatch.setattr("retracesoftware.ai_driver.replay_binary_path", lambda: "/fake/replay")
    monkeypatch.setattr(
        "retracesoftware.ai_driver.subprocess.run",
        lambda *a, **kw: SimpleNamespace(stdout=output, stderr="", returncode=1),
    )

    hint = _pytest_failure_hint(str(tmp_path / "case.retrace"))

    assert hint is not None
    assert hint["filename"] == str((tmp_path / "tests/test_partner_shape.py").resolve())
    assert hint["line"] == 7
    assert hint["function"] == "test_partner_shape_fails"
    assert hint["exception_type"] == "AssertionError"


@pytest.mark.skipif(
    sys.version_info < (3, 12),
    reason="raw-script failure-candidate search still uses sys.monitoring-backed search",
)
def test_pytest_failure_prelude_positions_dap_after_handled_import_noise(tmp_path):
    target = tmp_path / "minimal_exception_filter_case.py"
    target.write_text(
        "\n".join(
            [
                "try:",
                "    import definitely_missing_retrace_probe_module",
                "except ImportError:",
                "    pass",
                "",
                "sentinel = {'expected': 'skip caught import', 'actual': 'stop on assertion'}",
                "raise AssertionError('real failure after caught import')",
                "",
            ]
        ),
        encoding="utf-8",
    )
    trace = tmp_path / "case.retrace"

    record = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(trace),
            "--",
            str(target),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )

    assert record.returncode != 0
    assert trace.exists()

    hint = _pytest_failure_hint(str(trace))
    assert hint is not None
    assert hint["filename"] == str(target)
    assert hint["line"] == 7
    assert hint["exception_type"] == "AssertionError"

    executor = DAPExecutor(str(trace))
    try:
        prelude = _prime_pytest_failure_breakpoint(executor, hint)
        assert [step["tool"] for step in prelude] == [
            "start_replay_session",
            "set_exception_breakpoints",
            "set_breakpoints",
            "continue_execution",
            "get_stack_trace",
        ]
        assert prelude[1]["result"]["session"]["capabilities"]["exception_breakpoints"] == "disabled"

        stack = prelude[-1]["result"]["data"]["stack_frames"]
        assert stack[0]["source"]["path"] == str(target)
        assert stack[0]["line"] == 7

        repeated_start = executor.execute("start_replay_session", {})
        assert repeated_start["ok"] is True
        assert repeated_start["summary"] == "Retrace DAP replay session is already active."
        assert repeated_start["session"]["last_stop"]["reason"] == "breakpoint"

        scopes = executor.execute("get_scopes", {"frame_id": 0})
        assert scopes["ok"] is True
        ref = scopes["data"]["scopes"][0]["variables_reference"]
        variables = executor.execute("get_variables", {"variables_reference": ref})
        names = {item["name"]: item["value"] for item in variables["data"]["variables"]}
        assert names["sentinel"] == "{'expected': 'skip caught import', 'actual': 'stop on assertion'}"
    finally:
        executor.close()
