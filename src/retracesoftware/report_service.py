"""Minimal shareable report service core.

The service is intentionally dependency-free so it can sit behind whatever HTTP
runtime is fastest to deploy for the MVP.  The route dispatcher below mirrors
the hosted contract without requiring a web framework in the package.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from retracesoftware import share_reports


DEFAULT_BASE_URL = "https://retracesoftware.com"
DEFAULT_MAX_PAYLOAD_BYTES = 1024 * 1024
REPORT_MODES = {"public": "r", "full": "f"}
PATH_PREFIXES = {prefix: mode for mode, prefix in REPORT_MODES.items()}

_REPORT_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_FORBIDDEN_HTML_RE = re.compile(r"(?is)<\s*(script|iframe|object|embed)\b|javascript\s*:")
_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)([A-Za-z0-9._~+/=-]+)")
_DB_URL_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:([^@\s/]+)@")
_ASSIGNED_SECRET_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|token|secret|password)\s*[=:]\s*([^\s,'\"]+)"
)


class ReportServiceError(RuntimeError):
    """Raised when a report service request cannot be fulfilled."""

    def __init__(self, message: str, *, status: int = 400, code: str = "bad_request") -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


@dataclass(frozen=True)
class StoredReport:
    report_id: str
    mode: str
    report: dict[str, Any]
    html: str
    markdown: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class ServiceResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class ReportService:
    """File-backed implementation of the shareable report API contract."""

    def __init__(
        self,
        root: Path,
        *,
        api_keys: Iterable[str],
        base_url: str = DEFAULT_BASE_URL,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
    ) -> None:
        self.root = Path(root)
        self.api_keys = tuple(key for key in api_keys if key)
        self.base_url = base_url.rstrip("/")
        self.max_payload_bytes = max_payload_bytes

    def handle_request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | str = b"",
    ) -> ServiceResponse:
        headers = headers or {}
        try:
            method = method.upper()
            if isinstance(body, str):
                body_bytes = body.encode("utf-8")
            else:
                body_bytes = body
            clean_path = unquote(urlsplit(path).path)

            if method == "POST" and clean_path == "/api/reports":
                payload = self._decode_request_json(body_bytes)
                result = self.create_report(
                    payload,
                    authorization=_header(headers, "authorization"),
                )
                return _json_response(result, status=201)

            if method == "GET":
                return self._handle_get(clean_path)

            if method == "DELETE" and clean_path.startswith("/api/reports/"):
                report_id = clean_path.removeprefix("/api/reports/")
                self.delete_report(report_id, token=_bearer_token(_header(headers, "authorization")))
                return _json_response({"deleted": True})

            raise ReportServiceError("route not found", status=404, code="not_found")
        except ReportServiceError as exc:
            return _json_response({"error": exc.code, "message": exc.message}, status=exc.status)

    def create_report(self, payload: dict[str, Any], *, authorization: str | None) -> dict[str, Any]:
        self._authenticate(authorization)
        self._validate_payload_size(payload)
        mode = _string(payload.get("mode"))
        if mode not in REPORT_MODES:
            raise ReportServiceError("mode must be public or full")
        if payload.get("schema_version") != share_reports.SCHEMA_VERSION:
            raise ReportServiceError("unsupported report schema_version")
        if payload.get("visibility") != "unlisted":
            raise ReportServiceError("visibility must be unlisted")

        report = _dict(payload.get("report"))
        html = _string(payload.get("html"))
        markdown = _string(payload.get("markdown"))
        redaction_mode = _string(payload.get("redaction_mode"))
        if not report:
            raise ReportServiceError("report must be a JSON object")
        if not html:
            raise ReportServiceError("html must be a non-empty string")
        if not markdown:
            raise ReportServiceError("markdown must be a non-empty string")
        self._validate_html(html)
        self._validate_report_privacy(mode, report)

        if mode == "public" and _contains_unredacted_secret(payload):
            raise ReportServiceError(
                "public report contains an obvious unredacted secret",
                code="secret_detected",
            )
        if mode == "full" and share_reports.contains_critical_secret(payload):
            raise ReportServiceError(
                "full report contains an obvious private key or credential",
                code="secret_detected",
            )

        report_id = self._new_report_id()
        delete_token = secrets.token_urlsafe(32)
        canonical_report = dict(report)
        canonical_report["report_id"] = report_id
        report_url = self._report_url(mode, report_id)
        canonical_html = self._render_html(mode, canonical_report, report_url)
        canonical_markdown = self._render_markdown(mode, canonical_report)
        metadata = {
            "report_id": report_id,
            "mode": mode,
            "schema_version": share_reports.SCHEMA_VERSION,
            "created_at": _now_iso(),
            "visibility": "unlisted",
            "redaction_mode": redaction_mode,
            "delete_token_hash": _hash_token(delete_token),
            "deleted_at": None,
        }
        self._write_report(
            StoredReport(
                report_id=report_id,
                mode=mode,
                report=canonical_report,
                html=canonical_html,
                markdown=canonical_markdown,
                metadata=metadata,
            )
        )
        return {
            "report_id": report_id,
            "url": report_url,
            "delete_token": delete_token,
        }

    def get_report_html(self, mode: str, report_id: str) -> str:
        return self._read_report(mode, report_id).html

    def get_report_json(self, mode: str, report_id: str) -> dict[str, Any]:
        return self._read_report(mode, report_id).report

    def delete_report(self, report_id: str, *, token: str | None) -> None:
        _validate_report_id(report_id)
        report_dir, _mode = self._find_report_dir(report_id)
        metadata_path = report_dir / "metadata.json"
        metadata = _read_json(metadata_path)
        expected_hash = _string(metadata.get("delete_token_hash"))
        if not token or not expected_hash or not hmac.compare_digest(_hash_token(token), expected_hash):
            raise ReportServiceError("delete token is invalid", status=403, code="forbidden")
        metadata["deleted_at"] = metadata.get("deleted_at") or _now_iso()
        _write_json(metadata_path, metadata)

    def _handle_get(self, path: str) -> ServiceResponse:
        mode, report_id, wants_json = _parse_read_path(path)
        if wants_json:
            return _json_response(
                self.get_report_json(mode, report_id),
                headers={"Cache-Control": "public, max-age=300"},
            )
        html = self.get_report_html(mode, report_id)
        return ServiceResponse(
            status=200,
            headers={
                "Content-Type": "text/html; charset=utf-8",
                "Cache-Control": "public, max-age=300",
            },
            body=html.encode("utf-8"),
        )

    def _authenticate(self, authorization: str | None) -> None:
        if not self.api_keys:
            raise ReportServiceError(
                "report service has no upload API keys configured",
                status=503,
                code="api_keys_unconfigured",
            )
        token = _bearer_token(authorization)
        if not token:
            raise ReportServiceError("missing bearer token", status=401, code="unauthorized")
        if not any(hmac.compare_digest(token, key) for key in self.api_keys):
            raise ReportServiceError("invalid bearer token", status=401, code="unauthorized")

    def _validate_payload_size(self, payload: dict[str, Any]) -> None:
        try:
            encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ReportServiceError("payload must be JSON serializable") from exc
        if len(encoded) > self.max_payload_bytes:
            raise ReportServiceError("report payload is too large", status=413, code="payload_too_large")

    def _validate_html(self, html: str) -> None:
        if len(html.encode("utf-8")) > self.max_payload_bytes:
            raise ReportServiceError("html is too large", status=413, code="payload_too_large")
        if _FORBIDDEN_HTML_RE.search(html):
            raise ReportServiceError("html contains disallowed active content")
        if 'name="robots"' not in html or "noindex" not in html or "nofollow" not in html:
            raise ReportServiceError("html must include noindex,nofollow robots metadata")

    def _validate_report_privacy(self, mode: str, report: dict[str, Any]) -> None:
        privacy = _dict(report.get("privacy"))
        if mode == "public":
            if privacy.get("sanitized") is not True:
                raise ReportServiceError("public reports must declare privacy.sanitized=true")
            if privacy.get("tool_transcript_included") is not False:
                raise ReportServiceError(
                    "public reports must declare privacy.tool_transcript_included=false"
                )
            if privacy.get("trace_shared") is not False:
                raise ReportServiceError("public reports must declare privacy.trace_shared=false")
            return
        if privacy.get("trace_shared") is not False:
            raise ReportServiceError("full reports must declare privacy.trace_shared=false")

    def _render_html(self, mode: str, report: dict[str, Any], report_url: str) -> str:
        if mode == "public":
            return share_reports.render_public_html(report, report_url=report_url)
        return share_reports.render_full_html(report, report_url=report_url)

    def _render_markdown(self, mode: str, report: dict[str, Any]) -> str:
        if mode == "public":
            return share_reports.render_public_markdown(report)
        return share_reports.render_full_markdown(report)

    def _new_report_id(self) -> str:
        for _ in range(100):
            report_id = str(uuid4())
            if not any((self.root / mode / report_id).exists() for mode in REPORT_MODES):
                return report_id
        raise ReportServiceError("could not allocate report id", status=500, code="internal_error")

    def _write_report(self, stored: StoredReport) -> None:
        report_dir = self.root / stored.mode / stored.report_id
        report_dir.mkdir(parents=True, exist_ok=False)
        _write_json(report_dir / "report.json", stored.report)
        _write_json(report_dir / "metadata.json", stored.metadata)
        (report_dir / "report.html").write_text(stored.html, encoding="utf-8")
        (report_dir / "report.md").write_text(stored.markdown, encoding="utf-8")

    def _read_report(self, mode: str, report_id: str) -> StoredReport:
        if mode not in REPORT_MODES:
            raise ReportServiceError("mode must be public or full")
        _validate_report_id(report_id)
        report_dir = self.root / mode / report_id
        if not report_dir.is_dir():
            raise ReportServiceError("report not found", status=404, code="not_found")
        metadata = _read_json(report_dir / "metadata.json")
        if metadata.get("deleted_at"):
            raise ReportServiceError("report not found", status=404, code="not_found")
        report = _read_json(report_dir / "report.json")
        html = (report_dir / "report.html").read_text(encoding="utf-8")
        markdown = (report_dir / "report.md").read_text(encoding="utf-8")
        return StoredReport(
            report_id=report_id,
            mode=mode,
            report=report,
            html=html,
            markdown=markdown,
            metadata=metadata,
        )

    def _find_report_dir(self, report_id: str) -> tuple[Path, str]:
        for mode in REPORT_MODES:
            report_dir = self.root / mode / report_id
            if report_dir.is_dir():
                return report_dir, mode
        raise ReportServiceError("report not found", status=404, code="not_found")

    def _report_url(self, mode: str, report_id: str) -> str:
        return f"{self.base_url}/{REPORT_MODES[mode]}/{report_id}"

    def _decode_request_json(self, body: bytes) -> dict[str, Any]:
        if len(body) > self.max_payload_bytes:
            raise ReportServiceError("report payload is too large", status=413, code="payload_too_large")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReportServiceError("request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ReportServiceError("request body must be a JSON object")
        return payload


def _parse_read_path(path: str) -> tuple[str, str, bool]:
    parts = [part for part in path.split("/") if part]
    if len(parts) != 2 or parts[0] not in PATH_PREFIXES:
        raise ReportServiceError("route not found", status=404, code="not_found")
    raw_report_id = parts[1]
    wants_json = raw_report_id.endswith(".json")
    report_id = raw_report_id[:-5] if wants_json else raw_report_id
    _validate_report_id(report_id)
    return PATH_PREFIXES[parts[0]], report_id, wants_json


def _validate_report_id(report_id: str) -> None:
    if not _REPORT_ID_RE.match(report_id):
        raise ReportServiceError("invalid report id", status=404, code="not_found")


def _json_response(
    payload: dict[str, Any],
    *,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> ServiceResponse:
    response_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        response_headers.update(headers)
    return ServiceResponse(
        status=status,
        headers=response_headers,
        body=(json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _header(headers: dict[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value:
        return None
    return value.strip()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReportServiceError("report not found", status=404, code="not_found") from exc
    except json.JSONDecodeError as exc:
        raise ReportServiceError("stored report is corrupt", status=500, code="internal_error") from exc
    if not isinstance(payload, dict):
        raise ReportServiceError("stored report is corrupt", status=500, code="internal_error")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def _contains_unredacted_secret(value: Any) -> bool:
    for text in _walk_strings(value):
        if _PRIVATE_KEY_RE.search(text):
            return True
        for match in _BEARER_RE.finditer(text):
            if not _is_redacted(match.group(2)):
                return True
        for match in _DB_URL_RE.finditer(text):
            if not _is_redacted(match.group(1)):
                return True
        for match in _ASSIGNED_SECRET_RE.finditer(text):
            if not _is_redacted(match.group(1)):
                return True
    return False


def _is_redacted(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized in {"[redacted]", "[redacted_private_key]", "***", "****", "xxxxx", "xxxxxx"}
