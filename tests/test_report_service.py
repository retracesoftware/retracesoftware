import json
from pathlib import Path

import retracesoftware.report_service as report_service
import retracesoftware.share_reports as share_reports


FIXTURES = Path(__file__).parent / "fixtures"


def _artifact():
    return {
        "report": {
            "title": "Checkout failure",
            "status": "diagnosed",
            "summary": "Observed wrong total.",
            "root_cause": {"claim": "Discount basis was wrong.", "confidence": "high"},
            "evidence": [
                {
                    "claim": "discount_basis was percent",
                    "observed": "discount_basis='percent'",
                }
            ],
            "replay_walkthrough": [
                {"action": "inspect locals", "finding": "discount_basis='percent'"},
            ],
            "suggested_fix": {
                "summary": "Use cents for fixed discounts.",
                "test": "python -m pytest tests/test_checkout.py -q",
            },
            "limitations": [],
        },
        "transcript": [{"tool": "inspect", "result_summary": "ok", "ok": True}],
    }


def _dataframe_artifact():
    return json.loads((FIXTURES / "dataframe_ai_report.json").read_text(encoding="utf-8"))


def _payload(rendered):
    return {
        "mode": rendered.mode,
        "schema_version": share_reports.SCHEMA_VERSION,
        "report": json.loads(json.dumps(rendered.report)),
        "html": rendered.html,
        "markdown": rendered.markdown,
        "visibility": "unlisted",
        "redaction_mode": (
            "standard"
            if rendered.mode == "public"
            else "full_diagnostic_with_critical_secret_blocklist"
        ),
    }


def _post(service, payload, token="test-key"):
    return service.handle_request(
        "POST",
        "/api/reports",
        headers={"Authorization": f"Bearer {token}"},
        body=json.dumps(payload).encode("utf-8"),
    )


def test_public_report_post_stores_and_serves_html_and_json(tmp_path):
    service = report_service.ReportService(tmp_path, api_keys=["test-key"])
    rendered = share_reports.render_public_report(_artifact())
    payload = _payload(rendered)
    payload["html"] = rendered.html + "<p>Uploaded preview marker</p>"

    response = _post(service, payload)
    data = response.json()

    assert response.status == 201
    assert data["url"] == f"https://retracesoftware.com/r/{data['report_id']}"
    assert data["delete_token"]

    html_response = service.handle_request("GET", f"/r/{data['report_id']}")
    assert html_response.status == 200
    assert html_response.headers["Content-Type"] == "text/html; charset=utf-8"
    assert b"Retrace Resolution Report" in html_response.body
    assert b"Copy GitHub comment" in html_response.body
    assert f'href="https://retracesoftware.com/r/{data["report_id"]}.json"'.encode() in html_response.body
    assert f'data-copy="https://retracesoftware.com/r/{data["report_id"]}"'.encode() in html_response.body
    assert b"Uploaded preview marker" not in html_response.body

    json_response = service.handle_request("GET", f"/r/{data['report_id']}.json")
    stored_report = json_response.json()
    assert json_response.status == 200
    assert json_response.headers["Cache-Control"] == "public, max-age=300"
    assert stored_report["report_id"] == data["report_id"]
    assert stored_report["privacy"]["tool_transcript_included"] is False


def test_full_report_delete_token_deletes_report(tmp_path):
    service = report_service.ReportService(tmp_path, api_keys=["test-key"])
    rendered = share_reports.render_full_report(_artifact())
    create_response = _post(service, _payload(rendered))
    data = create_response.json()

    assert service.handle_request("GET", f"/f/{data['report_id']}").status == 200

    delete_response = service.handle_request(
        "DELETE",
        f"/api/reports/{data['report_id']}",
        headers={"Authorization": f"Bearer {data['delete_token']}"},
    )
    assert delete_response.status == 200

    missing_response = service.handle_request("GET", f"/f/{data['report_id']}")
    assert missing_response.status == 404


def test_report_upload_requires_api_key(tmp_path):
    service = report_service.ReportService(tmp_path, api_keys=["test-key"])
    rendered = share_reports.render_public_report(_artifact())

    response = service.handle_request("POST", "/api/reports", body=json.dumps(_payload(rendered)))

    assert response.status == 401
    assert response.json()["error"] == "unauthorized"


def test_report_upload_rejects_oversized_payload(tmp_path):
    service = report_service.ReportService(tmp_path, api_keys=["test-key"], max_payload_bytes=64)
    rendered = share_reports.render_public_report(_artifact())

    response = _post(service, _payload(rendered))

    assert response.status == 413
    assert response.json()["error"] == "payload_too_large"


def test_public_report_rejects_transcript_flag(tmp_path):
    service = report_service.ReportService(tmp_path, api_keys=["test-key"])
    rendered = share_reports.render_public_report(_artifact())
    payload = _payload(rendered)
    payload["report"]["privacy"]["tool_transcript_included"] = True

    response = _post(service, payload)

    assert response.status == 400
    assert "tool_transcript_included=false" in response.json()["message"]


def test_public_report_rejects_active_html(tmp_path):
    service = report_service.ReportService(tmp_path, api_keys=["test-key"])
    rendered = share_reports.render_public_report(_artifact())
    payload = _payload(rendered)
    payload["html"] = rendered.html + "<script>alert(1)</script>"

    response = _post(service, payload)

    assert response.status == 400
    assert "active content" in response.json()["message"]


def test_full_report_rejects_critical_secret(tmp_path):
    service = report_service.ReportService(tmp_path, api_keys=["test-key"])
    artifact = _artifact()
    artifact["report"]["summary"] = "-----BEGIN PRIVATE KEY----- secret"
    rendered = share_reports.render_full_report(artifact)

    response = _post(service, _payload(rendered))

    assert response.status == 400
    assert response.json()["error"] == "secret_detected"


def test_dataframe_fixture_upload_read_and_delete(tmp_path):
    service = report_service.ReportService(
        tmp_path,
        api_keys=["test-key"],
        base_url="https://reports.test",
    )
    artifact = _dataframe_artifact()

    public_rendered = share_reports.render_public_report(
        artifact,
        repo_root=Path("/workspace/dataframe-test-example"),
    )
    public_response = _post(service, _payload(public_rendered))
    public_data = public_response.json()
    assert public_response.status == 201

    public_html = service.handle_request("GET", f"/r/{public_data['report_id']}")
    assert public_html.status == 200
    assert b"DataFrame amount_gbp mismatch" in public_html.body
    assert b"DAP stack trace has 43 frame(s)" in public_html.body
    assert b"Copy GitHub comment" in public_html.body
    assert b"/workspace" not in public_html.body

    public_json = service.handle_request("GET", f"/r/{public_data['report_id']}.json").json()
    assert public_json["privacy"]["tool_transcript_included"] is False
    assert public_json["how_retrace_found_this"][:2] == [
        "Started Retrace DAP replay session and stopped at entry.",
        "DAP setBreakpoints completed.",
    ]

    delete_response = service.handle_request(
        "DELETE",
        f"/api/reports/{public_data['report_id']}",
        headers={"Authorization": f"Bearer {public_data['delete_token']}"},
    )
    assert delete_response.status == 200
    assert service.handle_request("GET", f"/r/{public_data['report_id']}").status == 404

    full_rendered = share_reports.render_full_report(artifact)
    full_response = _post(service, _payload(full_rendered))
    full_data = full_response.json()
    assert full_response.status == 201

    full_json = service.handle_request("GET", f"/f/{full_data['report_id']}.json").json()
    assert full_json["privacy"]["tool_transcript_included"] is True
    assert full_json["tool_transcript"][0]["tool"] == "start_replay_session"
    assert "/workspace/dataframe-test-example/tests/test_financial_report.py" in json.dumps(full_json)
