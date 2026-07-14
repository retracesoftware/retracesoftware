from retracesoftware.ai_driver import _render_markdown


def test_render_markdown_preserves_detailed_ai_report_sections():
    markdown = _render_markdown(
        {
            "report": {
                "status": "diagnosed",
                "title": "Wrong closing FX rate selected",
                "summary": "The report used an older EUR rate, producing the failing amount_gbp value.",
                "root_cause": {
                    "claim": "The query is unordered while report.py selects rates.iloc[-1].",
                    "confidence": "high",
                    "why": "Replay showed closing_rate=0.87905 at the selection line.",
                },
                "evidence": [
                    {
                        "claim": "The implementation selects the final returned row.",
                        "tool": "get_source_context",
                        "location": {"path": "/app/report.py", "line": 52},
                        "observed": 'closing_rate = float(rates["rate"].iloc[-1])',
                    }
                ],
                "replay_walkthrough": [
                    {
                        "step": 1,
                        "action": "evaluate_expression",
                        "finding": "result amount_gbp was 63750.41 while expected was 59463.2.",
                    }
                ],
                "suggested_fix": {
                    "summary": "Order rates by rate_date before selecting the closing row.",
                    "files": [
                        {
                            "path": "/app/report.py",
                            "line": 52,
                            "change": "Select the rate for the latest date deterministically.",
                        }
                    ],
                    "test": "Assert that shuffled FX rows still select the period-end rate.",
                },
                "reproducibility": {
                    "data_dependency": "observed",
                    "intermittency": "not_observed",
                    "determinism": "deterministic",
                    "confidence": "high",
                    "why": "The replay selected the same recorded row and value.",
                },
                "open_questions": ["Does the SQL query guarantee ordering on every backend?"],
                "limitations": ["Only one recorded dataset was inspected."],
            },
            "transcript": [
                {
                    "tool": "get_source_context",
                    "result": {"summary": "Read report.py around line 52."},
                }
            ],
        }
    )

    assert "**Status:** diagnosed" in markdown
    assert "## Root Cause" in markdown
    assert "**Confidence:** high" in markdown
    assert "## Evidence" in markdown
    assert "`get_source_context`" in markdown
    assert "`/app/report.py:52`" in markdown
    assert "## Replay Walkthrough" in markdown
    assert "## Suggested Fix" in markdown
    assert "**Regression test:**" in markdown
    assert "## Reproducibility" in markdown
    assert "## Open Questions" in markdown
    assert "## Limitations" in markdown
    assert "{'claim':" not in markdown
