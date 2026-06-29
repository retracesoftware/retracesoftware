import json

from retracesoftware import cli


def _artifact():
    return {
        "report": {
            "title": "Checkout failure",
            "status": "diagnosed",
            "summary": "Observed wrong total.",
            "root_cause": {"claim": "Discount basis was wrong.", "confidence": "high"},
            "evidence": [{"claim": "discount_basis was percent", "observed": "discount_basis='percent'"}],
            "suggested_fix": {"summary": "Use cents for fixed discounts.", "test": "python -m pytest tests/test_checkout.py -q"},
            "limitations": [],
        },
        "transcript": [{"tool": "inspect", "result_summary": "ok", "ok": True}],
    }


def test_report_share_writes_public_preview_without_api_key(monkeypatch, tmp_path, capsys):
    input_path = tmp_path / "failure.ai-report.json"
    input_path.write_text(json.dumps(_artifact()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("RETRACE_API_KEY", raising=False)

    exit_code = cli.main(["report", "--json", str(input_path), "--share"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Using AI report artifact" in captured.out
    assert "Public report preview written" in captured.out
    assert "RETRACE_API_KEY is not configured" in captured.err
    assert (tmp_path / "retrace-report.public.json").is_file()
    assert (tmp_path / "retrace-report.public.html").is_file()
    public_report = json.loads((tmp_path / "retrace-report.public.json").read_text(encoding="utf-8"))
    assert public_report["privacy"]["tool_transcript_included"] is False


def test_report_share_prints_delete_token_and_command(monkeypatch, tmp_path, capsys):
    input_path = tmp_path / "failure.ai-report.json"
    input_path.write_text(json.dumps(_artifact()), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RETRACE_API_KEY", "test-key")

    def fake_upload(rendered, *, api_key, endpoint):
        assert rendered.mode == "public"
        assert api_key == "test-key"
        assert endpoint == "https://retracesoftware.test/api/reports"
        return cli.share_reports.UploadResult(
            report_id="report-123",
            url="https://retracesoftware.test/r/report-123",
            delete_token="delete-secret",
        )

    monkeypatch.setattr(cli.share_reports, "upload_report", fake_upload)

    exit_code = cli.main([
        "report",
        "--json",
        str(input_path),
        "--share",
        "--endpoint",
        "https://retracesoftware.test/api/reports",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "https://retracesoftware.test/r/report-123" in captured.out
    assert "Delete token:\ndelete-secret" in captured.out
    assert (
        "curl -X DELETE -H 'Authorization: Bearer delete-secret' "
        "https://retracesoftware.test/api/reports/report-123"
    ) in captured.out


def test_report_share_full_requires_noninteractive_confirmation(tmp_path, capsys):
    input_path = tmp_path / "failure.ai-report.json"
    input_path.write_text(json.dumps(_artifact()), encoding="utf-8")

    exit_code = cli.main(["report", "--json", str(input_path), "--share-full"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "--share-full requires --yes-share-full" in captured.err


def test_report_share_full_blocks_critical_secret_upload(monkeypatch, tmp_path, capsys):
    artifact = _artifact()
    artifact["report"]["summary"] = "-----BEGIN PRIVATE KEY----- secret"
    input_path = tmp_path / "failure.ai-report.json"
    input_path.write_text(json.dumps(artifact), encoding="utf-8")
    monkeypatch.setenv("RETRACE_API_KEY", "test-key")

    exit_code = cli.main([
        "report",
        "--json",
        str(input_path),
        "--share-full",
        "--yes-share-full",
    ])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "full report upload blocked" in captured.err


def test_report_server_requires_api_key(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("RETRACE_API_KEY", raising=False)
    monkeypatch.delenv("RETRACE_REPORT_API_KEYS", raising=False)

    exit_code = cli.main([
        "report-server",
        "--storage-root",
        str(tmp_path),
        "--port",
        "0",
    ])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert "set --api-key" in captured.err


def test_report_server_cli_starts_and_closes(monkeypatch, tmp_path, capsys):
    class FakeServer:
        server_address = ("127.0.0.1", 4321)

        def __init__(self):
            self.served = False
            self.closed = False

        def serve_forever(self):
            self.served = True

        def server_close(self):
            self.closed = True

    fake_server = FakeServer()

    def fake_create_server(host, port, service, *, log_requests):
        assert host == "127.0.0.1"
        assert port == 0
        assert log_requests is False
        assert service.base_url == "http://127.0.0.1:0"
        return fake_server

    monkeypatch.delenv("RETRACE_REPORT_API_KEYS", raising=False)
    monkeypatch.setenv("RETRACE_API_KEY", "test-key")
    monkeypatch.setattr(cli.report_server, "create_server", fake_create_server)

    exit_code = cli.main([
        "report-server",
        "--storage-root",
        str(tmp_path),
        "--port",
        "0",
    ])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert fake_server.served is True
    assert fake_server.closed is True
    assert "Upload endpoint: http://127.0.0.1:4321/api/reports" in captured.out
