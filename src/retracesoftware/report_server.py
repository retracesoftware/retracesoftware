"""HTTP adapter for the shareable report service."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from retracesoftware.report_service import ReportService, ServiceResponse


def create_server(
    host: str,
    port: int,
    service: ReportService,
    *,
    log_requests: bool = False,
) -> ThreadingHTTPServer:
    handler = make_handler(service, log_requests=log_requests)
    return ThreadingHTTPServer((host, port), handler)


def make_handler(service: ReportService, *, log_requests: bool = False) -> type[BaseHTTPRequestHandler]:
    class ReportRequestHandler(BaseHTTPRequestHandler):
        server_version = "RetraceReportService/0.1"

        def do_GET(self) -> None:
            self._handle()

        def do_POST(self) -> None:
            self._handle()

        def do_DELETE(self) -> None:
            self._handle()

        def _handle(self) -> None:
            body = self._read_body()
            if body is None:
                response = _payload_too_large_response()
            else:
                response = service.handle_request(
                    self.command,
                    self.path,
                    headers={key: value for key, value in self.headers.items()},
                    body=body,
                )
            self._send_service_response(response)

        def _read_body(self) -> bytes | None:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError:
                length = 0
            if length <= 0:
                return b""
            if length > service.max_payload_bytes:
                return None
            return self.rfile.read(length)

        def _send_service_response(self, response: ServiceResponse) -> None:
            self.send_response(response.status)
            headers = dict(response.headers)
            headers["Content-Length"] = str(len(response.body))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            if response.body:
                self.wfile.write(response.body)

        def log_message(self, format: str, *args: Any) -> None:
            if log_requests:
                super().log_message(format, *args)

    return ReportRequestHandler


def _payload_too_large_response() -> ServiceResponse:
    body = b'{\n  "error": "payload_too_large",\n  "message": "report payload is too large"\n}\n'
    return ServiceResponse(
        status=413,
        headers={"Content-Type": "application/json; charset=utf-8"},
        body=body,
    )
