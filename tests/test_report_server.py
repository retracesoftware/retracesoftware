import io
import json

import retracesoftware.report_server as report_server
import retracesoftware.share_reports as share_reports
from retracesoftware.report_service import ReportService


def _artifact():
    return {
        "report": {
            "title": "Checkout failure",
            "status": "diagnosed",
            "summary": "Observed wrong total.",
            "root_cause": {"claim": "Discount basis was wrong.", "confidence": "high"},
            "evidence": [{"claim": "discount_basis was percent", "observed": "redacted"}],
            "suggested_fix": {"summary": "Use cents for fixed discounts."},
        },
        "transcript": [{"tool": "inspect", "result_summary": "ok", "ok": True}],
    }


class _FakeSocket:
    def __init__(self, request: bytes):
        self._request = io.BytesIO(request)
        self.response = io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        if "r" in mode:
            return self._request
        return self.response

    def sendall(self, data):
        self.response.write(data)


def _raw_request(method, path, *, body=b"", headers=None):
    headers = headers or {}
    lines = [f"{method} {path} HTTP/1.0", "Host: retrace.test"]
    for key, value in headers.items():
        lines.append(f"{key}: {value}")
    if body:
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + body


def _handle(handler, request: bytes):
    fake_socket = _FakeSocket(request)
    handler(fake_socket, ("127.0.0.1", 12345), object())
    raw_response = fake_socket.response.getvalue()
    head, body = raw_response.split(b"\r\n\r\n", 1)
    status = int(head.split(b" ", 2)[1])
    headers = {}
    for line in head.split(b"\r\n")[1:]:
        key, value = line.decode("iso-8859-1").split(": ", 1)
        headers[key.lower()] = value
    return status, headers, body


def test_http_handler_round_trips_upload_and_read(tmp_path):
    api_key = "test-key"
    service = ReportService(tmp_path, api_keys=[api_key], base_url="http://retrace.test")
    handler = report_server.make_handler(service)
    rendered = share_reports.render_public_report(_artifact())
    body = json.dumps({
        "mode": rendered.mode,
        "schema_version": share_reports.SCHEMA_VERSION,
        "report": rendered.report,
        "html": rendered.html,
        "markdown": rendered.markdown,
        "visibility": "unlisted",
        "redaction_mode": "standard",
    }).encode("utf-8")

    status, headers, response_body = _handle(
        handler,
        _raw_request(
            "POST",
            "/api/reports",
            body=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        ),
    )
    upload = json.loads(response_body.decode("utf-8"))

    assert status == 201
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert upload["url"] == f"http://retrace.test/r/{upload['report_id']}"

    status, headers, response_body = _handle(
        handler,
        _raw_request("GET", f"/r/{upload['report_id']}.json"),
    )
    payload = json.loads(response_body.decode("utf-8"))
    assert status == 200
    assert headers["cache-control"] == "public, max-age=300"
    assert payload["report_id"] == upload["report_id"]
    assert payload["privacy"]["tool_transcript_included"] is False

    status, headers, response_body = _handle(
        handler,
        _raw_request("GET", f"/r/{upload['report_id']}"),
    )
    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert "Retrace Resolution Report" in response_body.decode("utf-8")


def test_http_handler_rejects_oversized_upload_before_reading_body(tmp_path):
    service = ReportService(tmp_path, api_keys=["test-key"], max_payload_bytes=4)
    handler = report_server.make_handler(service)

    status, headers, response_body = _handle(
        handler,
        _raw_request(
            "POST",
            "/api/reports",
            body=b"abcde",
            headers={"Authorization": "Bearer test-key"},
        ),
    )

    assert status == 413
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert json.loads(response_body.decode("utf-8"))["error"] == "payload_too_large"
