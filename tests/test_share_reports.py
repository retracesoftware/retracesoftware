import json
from pathlib import Path

import retracesoftware.share_reports as share_reports


FIXTURES = Path(__file__).parent / "fixtures"


def _artifact():
    return {
        "report": {
            "title": "Bad branch <script>alert(1)</script>",
            "status": "diagnosed",
            "investigation_target": "tests/test_checkout.py::test_total",
            "failure_category": "assertion",
            "summary": "Runtime used password=supersecret at /Users/alice/project/src/app.py",
            "root_cause": {
                "claim": "The wrong branch ran with token=abc123.",
                "confidence": "high",
                "why": "Observed branch='legacy'.",
                "location": {
                    "path": "/Users/alice/project/src/app.py",
                    "line": 42,
                    "function": "calculate_total",
                },
            },
            "evidence": [
                {
                    "claim": "Runtime local exposed Authorization: Bearer abc.def",
                    "tool": "get_stack_trace",
                    "location": {"path": "/Users/alice/project/src/app.py", "line": 42},
                    "observed": "payload=<script>alert(1)</script>",
                },
            ],
            "replay_walkthrough": [
                {"action": "inspect locals", "finding": "branch='legacy'"},
            ],
            "suggested_fix": {
                "summary": "Use the current branch.",
                "test": "python -m pytest tests/test_checkout.py -q",
            },
            "limitations": ["Only one failing run was inspected."],
        },
        "tool_calls": 2,
        "model_turns": 1,
        "final_session": {"state": "stopped"},
        "transcript": [{"tool": "get_stack_trace", "result_summary": "ok", "ok": True}],
    }


def _dataframe_artifact():
    return json.loads((FIXTURES / "dataframe_ai_report.json").read_text(encoding="utf-8"))


def test_public_report_redacts_paths_and_secrets_and_escapes_html(tmp_path):
    repo = tmp_path / "project"
    repo.mkdir()
    artifact = _artifact()
    artifact["report"]["summary"] = f"Runtime used password=supersecret at {repo}/src/app.py"
    artifact["report"]["root_cause"]["location"]["path"] = f"{repo}/src/app.py"

    rendered = share_reports.render_public_report(artifact, repo_root=repo)

    report_text = json.dumps(rendered.report)
    assert "supersecret" not in report_text
    assert "Bearer abc.def" not in report_text
    assert str(repo) not in report_text
    assert "src/app.py" in report_text

    assert "<script>alert(1)</script>" not in rendered.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered.html
    assert "Tool transcript: Hidden" in rendered.html


def test_public_html_with_url_has_share_actions_and_json_download():
    rendered = share_reports.render_public_report(_artifact())
    html = share_reports.render_public_html(
        rendered.report,
        report_url="https://retracesoftware.com/r/report-123",
    )

    assert 'property="og:url" content="https://retracesoftware.com/r/report-123"' in html
    assert 'href="https://retracesoftware.com/r/report-123.json"' in html
    assert 'data-copy="https://retracesoftware.com/r/report-123"' in html
    assert "Copy GitHub comment" in html
    assert "Anyone with this link can view this public report." in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_demo_style_evidence_summary_is_rendered():
    artifact = {
        "report": {
            "title": "DataFrame amount_gbp mismatch",
            "status": "complete",
            "summary": "Recorded pytest failure.",
            "root_cause": {
                "claim": "The dataframe mismatch is real.",
                "confidence": "medium",
            },
            "evidence": [
                {
                    "source": "retrace-dap",
                    "summary": "DAP stack trace has 43 frame(s); top frame is test_financial_report.py:26.",
                }
            ],
        }
    }

    public = share_reports.render_public_report(artifact)
    full = share_reports.render_full_report(artifact)

    assert "DAP stack trace has 43 frame(s)" in public.markdown
    assert "DAP stack trace has 43 frame(s)" in public.html
    assert "DAP stack trace has 43 frame(s)" in full.markdown
    assert "DAP stack trace has 43 frame(s)" in full.html


def test_demo_style_nested_transcript_summary_is_rendered():
    artifact = {
        "report": {
            "title": "DataFrame amount_gbp mismatch",
            "status": "complete",
            "summary": "Recorded pytest failure.",
        },
        "transcript": [
            {
                "tool": "get_stack_trace",
                "result": {
                    "summary": "DAP stack trace has 43 frame(s); top frame is test_financial_report.py:26."
                },
            }
        ],
    }

    full = share_reports.render_full_report(artifact)

    assert "get_stack_trace" in full.markdown
    assert "DAP stack trace has 43 frame(s)" in full.markdown
    assert "DAP stack trace has 43 frame(s)" in full.html


def test_public_walkthrough_uses_transcript_summaries():
    artifact = {
        "report": {
            "title": "DataFrame amount_gbp mismatch",
            "status": "complete",
            "summary": "Recorded pytest failure.",
        },
        "transcript": [
            {
                "tool": "start_replay_session",
                "result": {"summary": "Started Retrace DAP replay session and stopped at entry."},
            },
            {
                "tool": "get_stack_trace",
                "result": {
                    "summary": "DAP stack trace has 43 frame(s); top frame is test_financial_report.py:26."
                },
            },
        ],
    }

    public = share_reports.render_public_report(artifact)

    assert public.report["how_retrace_found_this"] == [
        "Started Retrace DAP replay session and stopped at entry.",
        "DAP stack trace has 43 frame(s); top frame is test_financial_report.py:26.",
    ]
    assert "DAP stack trace has 43 frame(s)" in public.markdown


def test_public_walkthrough_uses_evidence_when_transcript_is_missing():
    artifact = {
        "report": {
            "title": "DataFrame amount_gbp mismatch",
            "status": "complete",
            "summary": "Recorded pytest failure.",
            "evidence": [
                {
                    "source": "retrace-dap",
                    "summary": "Read 4 variable(s) through DAP.",
                }
            ],
        }
    }

    public = share_reports.render_public_report(artifact)

    assert public.report["how_retrace_found_this"] == ["Read 4 variable(s) through DAP."]


def test_full_report_preserves_transcript_and_detects_critical_secret():
    artifact = _artifact()
    artifact["report"]["summary"] = "-----BEGIN PRIVATE KEY----- secret"

    rendered = share_reports.render_full_report(artifact)

    assert rendered.report["tool_transcript"][0]["tool"] == "get_stack_trace"
    assert share_reports.contains_critical_secret(rendered.report) is True


def test_full_html_with_url_has_warning_and_json_download():
    rendered = share_reports.render_full_report(_artifact())
    html = share_reports.render_full_html(
        rendered.report,
        report_url="https://retracesoftware.com/f/report-456",
    )

    assert "Anyone with this link can view this full diagnostic report." in html
    assert 'href="https://retracesoftware.com/f/report-456.json"' in html
    assert 'data-copy="https://retracesoftware.com/f/report-456"' in html


def test_dataframe_fixture_public_report_is_sanitized_and_replay_specific():
    artifact = _dataframe_artifact()

    rendered = share_reports.render_public_report(
        artifact,
        repo_root=Path("/workspace/dataframe-test-example"),
    )
    html = share_reports.render_public_html(
        rendered.report,
        report_url="https://retracesoftware.com/r/dataframe-fixture",
    )
    report_text = json.dumps(rendered.report, sort_keys=True)

    assert rendered.report["title"] == "DataFrame amount_gbp mismatch"
    assert rendered.report["failure"]["category"] == "dataframe_assertion_mismatch"
    assert "amount_gbp" in rendered.markdown
    assert "DAP stack trace has 43 frame(s)" in rendered.markdown
    assert "Read 4 variable(s) through DAP." in rendered.markdown
    assert "Started Retrace DAP replay session" in rendered.markdown
    assert "Tool transcript: Hidden" in rendered.markdown
    assert "tool_transcript" not in rendered.report
    assert "/workspace" not in report_text
    assert "Evidence item" not in rendered.markdown
    assert "Copy GitHub comment" in html
    assert 'href="https://retracesoftware.com/r/dataframe-fixture.json"' in html


def test_dataframe_fixture_full_report_preserves_diagnostic_details():
    artifact = _dataframe_artifact()

    rendered = share_reports.render_full_report(artifact)
    report_text = json.dumps(rendered.report, sort_keys=True)

    assert rendered.report["title"] == "DataFrame amount_gbp mismatch"
    assert rendered.report["privacy"]["tool_transcript_included"] is True
    assert rendered.report["tool_transcript"][0]["tool"] == "start_replay_session"
    assert "/workspace/dataframe-test-example/tests/test_financial_report.py" in report_text
    assert "63750.41" in report_text
    assert "59463.2" in report_text
    assert "DAP stack trace has 43 frame(s)" in rendered.markdown
    assert "Full Tool Transcript" in rendered.markdown
