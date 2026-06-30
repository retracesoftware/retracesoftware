from types import SimpleNamespace

import pytest

from retracesoftware.ai_driver import (
    DAPExecutor,
    DAPRequestError,
    _application_dap_frames,
    _dap_error,
    _exception_and_frames_from_pytest_hint,
    _parse_dap_error_response,
    _pytest_failure_hint_from_output,
)


def test_parse_dap_error_response_preserves_retrace_category():
    exc = _parse_dap_error_response(
        {
            "command": "stackTrace",
            "success": False,
            "message": "not_stopped: stopped-state inspection is unavailable",
            "body": {
                "retrace": {
                    "category": "inspection_unavailable",
                    "code": "not_stopped",
                    "control_method": "stack",
                }
            },
        },
        "",
        "",
    )
    assert exc.category == "inspection_unavailable"
    assert exc.code == "not_stopped"
    assert exc.control_method == "stack"


def test_application_dap_frames_drop_pytest_and_retrace_internals():
    frames = [
        {
            "name": "pytest_internal",
            "line": 2023,
            "source": {"path": "/venv/lib/python3.11/site-packages/_pytest/config/__init__.py"},
        },
        {
            "name": "test_case",
            "line": 30,
            "source": {"path": "/tmp/project/unit_tests/test_period_rates.py"},
        },
        {
            "name": "_period_rates",
            "line": 12,
            "source": {"path": "/tmp/project/report_test_demo/report.py"},
        },
    ]

    selected = _application_dap_frames(frames)

    assert [frame["name"] for frame in selected] == ["test_case", "_period_rates"]


def test_get_stack_trace_recovers_application_frames_when_dap_inspection_unavailable(monkeypatch):
    executor = object.__new__(DAPExecutor)
    executor.trace = "/tmp/trace.retrace"
    executor.replay_bin = "/tmp/replay"

    session = SimpleNamespace(
        synthetic_exception=None,
        frames=[],
        output="",
        closed=False,
        state={"state": "stopped", "last_stop": {"reason": "breakpoint"}},
    )
    executor.session = session

    def fake_request(command, arguments=None):
        raise DAPRequestError(
            "not_stopped: stopped-state inspection is unavailable",
            command=command,
            category="inspection_unavailable",
            code="not_stopped",
            control_method="stack",
        )

    session.request = fake_request
    monkeypatch.setattr(
        executor,
        "_recover_application_traceback",
        lambda _session: (
            {"type": "AssertionError", "message": "assert 0.87905 == 0.819934"},
            [
                {
                    "id": 0,
                    "name": "test_period_rates_uses_latest_date_for_closing_rate",
                    "source": {"path": "/tmp/project/unit_tests/test_period_rates.py"},
                    "line": 30,
                    "column": 0,
                },
                {
                    "id": 1,
                    "name": "_period_rates",
                    "source": {"path": "/tmp/project/report_test_demo/report.py"},
                    "line": 12,
                    "column": 0,
                },
            ],
        ),
    )

    result = executor.get_stack_trace({"thread_id": 1})

    assert result["ok"] is True
    assert result["data"]["source"] == "replay.traceback"
    assert [frame["name"] for frame in result["data"]["stack_frames"]] == [
        "test_period_rates_uses_latest_date_for_closing_rate",
        "_period_rates",
    ]
    assert "recovery_note" in result["data"]
    assert session.frames[0]["source"]["path"].endswith("unit_tests/test_period_rates.py")


def test_get_stack_trace_filters_pytest_frames_from_successful_dap_response(monkeypatch):
    executor = object.__new__(DAPExecutor)
    session = SimpleNamespace(
        synthetic_exception=None,
        frames=[],
        output="",
        closed=False,
        state={"state": "stopped"},
    )
    executor.session = session

    def fake_request(command, arguments=None):
        return {
            "body": {
                "stackFrames": [
                    {
                        "id": 0,
                        "name": "pytest_raise",
                        "line": 2023,
                        "source": {
                            "path": "/venv/lib/python3.11/site-packages/_pytest/config/__init__.py",
                        },
                    },
                    {
                        "id": 1,
                        "name": "test_period_rates_uses_latest_date_for_closing_rate",
                        "line": 30,
                        "source": {"path": "/tmp/project/unit_tests/test_period_rates.py"},
                    },
                ],
                "totalFrames": 2,
            }
        }

    session.request = fake_request
    monkeypatch.setattr(
        executor,
        "_recover_application_traceback",
        lambda _session: (None, []),
    )

    result = executor.get_stack_trace({"thread_id": 1})

    assert result["ok"] is True
    assert len(result["data"]["stack_frames"]) == 1
    assert result["data"]["stack_frames"][0]["name"] == "test_period_rates_uses_latest_date_for_closing_rate"


def test_pytest_failure_hint_from_short_output_matches_issue_75_format(tmp_path):
    output = """
FAILED unit_tests/test_period_rates.py::test_period_rates_uses_latest_date_for_closing_rate
E       assert 0.87905 == 0.819934

unit_tests/test_period_rates.py:30: AssertionError
"""
    hint = _pytest_failure_hint_from_output(output, cwd=tmp_path)

    assert hint is not None
    assert hint["filename"] == str((tmp_path / "unit_tests/test_period_rates.py").resolve())
    assert hint["line"] == 30
    assert hint["function"] == "test_period_rates_uses_latest_date_for_closing_rate"
    assert hint["exception_type"] == "AssertionError"


def test_exception_and_frames_from_pytest_hint_builds_application_frame(tmp_path):
    hint = {
        "filename": str(tmp_path / "unit_tests/test_period_rates.py"),
        "line": 30,
        "function": "test_period_rates_uses_latest_date_for_closing_rate",
        "exception_type": "AssertionError",
        "exception_message": "assert 0.87905 == 0.819934",
    }
    exception, frames = _exception_and_frames_from_pytest_hint(hint, "")

    assert exception == {"type": "AssertionError", "message": "assert 0.87905 == 0.819934"}
    assert len(frames) == 1
    assert frames[0]["line"] == 30
    assert frames[0]["name"] == "test_period_rates_uses_latest_date_for_closing_rate"


def test_dap_error_maps_inspection_unavailable_to_application_guidance():
    exc = DAPRequestError(
        "not_stopped: stopped-state inspection is unavailable",
        category="inspection_unavailable",
        code="not_stopped",
    )
    result = _dap_error("get_scopes", exc, {"state": "stopped"})

    assert result["ok"] is False
    assert result["error"]["domain"] == "application"
    assert result["error"]["category"] == "wrong_stop_location"
    assert "application code" in result["error"]["message"]
