"""Shareable Retrace report models and renderers."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = "retrace.resolution_report.v1"
DEFAULT_REPORT_ENDPOINT = "https://retracesoftware.com/api/reports"
PUBLIC_JSON_DEFAULT = "retrace-report.public.json"
PUBLIC_HTML_DEFAULT = "retrace-report.public.html"

_PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
_BEARER_RE = re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[A-Za-z0-9._~+/=-]+")
_API_KEY_RE = re.compile(r"(?i)\b((?:api[_-]?key|token|secret)\s*[=:]\s*)([^\s,'\"]+)")
_PASSWORD_RE = re.compile(r"(?i)\b(password\s*[=:]\s*)([^\s,'\"]+)")
_DB_URL_RE = re.compile(r"\b([a-z][a-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@")
_ABSOLUTE_PATH_RE = re.compile(r"(?P<path>/(?:Users|private|tmp|var|home|opt)/[^\s,;)`'\"]+)")


class ReportError(RuntimeError):
    """Raised for shareable report generation failures."""


@dataclass(frozen=True)
class RenderedReport:
    mode: str
    report: dict[str, Any]
    markdown: str
    html: str


@dataclass(frozen=True)
class UploadResult:
    report_id: str
    url: str
    delete_token: str | None = None


def load_artifact(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ReportError(f"AI report artifact not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ReportError(f"AI report artifact is not valid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReportError("AI report artifact must be a JSON object")
    return payload


def build_full_report(
    artifact: dict[str, Any],
    *,
    report_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    source = _source_report(artifact)
    report_id = report_id or str(uuid4())
    created_at = created_at or _now_iso()
    return {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "mode": "full",
        "created_at": created_at,
        "title": _string(source.get("title")) or "Retrace Diagnostic Report",
        "status": _string(source.get("status")),
        "summary": _string(source.get("summary")),
        "root_cause": _dict(source.get("root_cause")),
        "evidence": _list(source.get("evidence")),
        "replay_walkthrough": _list(source.get("replay_walkthrough")),
        "suggested_fix": _dict(source.get("suggested_fix")),
        "open_questions": _list(source.get("open_questions")),
        "limitations": _list(source.get("limitations")),
        "tool_transcript": _list(artifact.get("transcript")),
        "raw_metadata": {
            "tool_calls": artifact.get("tool_calls", 0),
            "model_turns": artifact.get("model_turns", 0),
            "final_session": _dict(artifact.get("final_session")),
            "tool_results": _list(artifact.get("tool_results")),
            "investigation_target": _string(source.get("investigation_target")),
            "failure_domain": _string(source.get("failure_domain")),
            "failure_category": _string(source.get("failure_category")),
        },
        "privacy": {
            "sanitized": False,
            "trace_shared": False,
            "runtime_values_included": True,
            "tool_transcript_included": True,
        },
    }


def build_public_report(
    artifact: dict[str, Any],
    *,
    repo_root: Path | None = None,
    redaction: str = "standard",
    report_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    if redaction not in {"standard", "strict"}:
        raise ReportError("redaction must be standard or strict")
    source = _source_report(artifact)
    report_id = report_id or str(uuid4())
    created_at = created_at or _now_iso()
    root_cause = _dict(source.get("root_cause"))
    suggested_fix = _dict(source.get("suggested_fix"))
    sanitizer = _Sanitizer(repo_root=repo_root, redaction=redaction)

    report = {
        "schema_version": SCHEMA_VERSION,
        "report_id": report_id,
        "mode": "public",
        "created_at": created_at,
        "title": _string(source.get("title")) or "Retrace Resolution Report",
        "status": _string(source.get("status")),
        "confidence": {
            "level": _string(root_cause.get("confidence")),
            "reason": _string(root_cause.get("why")),
        },
        "failure": {
            "category": _string(source.get("failure_category") or source.get("failure_domain")),
            "test": _string(source.get("investigation_target")),
            "location": _location_from(root_cause) or _location_from_first_evidence(source),
        },
        "root_cause": {
            "summary": _string(root_cause.get("claim") or root_cause.get("summary")),
            "location": _location_from(root_cause),
        },
        "summary": _string(source.get("summary")),
        "evidence": _public_evidence(source),
        "suggested_fix": {
            "summary": _string(suggested_fix.get("summary")),
            "files": _list(suggested_fix.get("files")),
            "test": _string(suggested_fix.get("test")),
        },
        "validation": {
            "commands": _validation_commands(suggested_fix),
        },
        "limitations": _list(source.get("limitations")),
        "how_retrace_found_this": _public_walkthrough(source, artifact),
        "privacy": {
            "sanitized": True,
            "trace_shared": False,
            "runtime_values_included": True,
            "tool_transcript_included": False,
        },
    }
    return sanitizer.sanitize(report)


def render_public_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Retrace Resolution Report",
        "",
        f"## {_text(report.get('title'))}",
        "",
        "Retrace replayed the failed execution and found the runtime evidence behind this bug.",
        "",
        f"- Status: {_text(report.get('status'))}",
        f"- Confidence: {_text(_dict(report.get('confidence')).get('level'))}",
        f"- Failure category: {_text(_dict(report.get('failure')).get('category'))}",
        f"- Generated: {_text(report.get('created_at'))}",
        "",
    ]
    _append_section(lines, "What Happened", _text(report.get("summary")))
    root = _dict(report.get("root_cause"))
    _append_section(lines, "Root Cause", _text(root.get("summary")))
    evidence = _list(report.get("evidence"))
    if evidence:
        lines.extend(["## Evidence", ""])
        for item in evidence:
            evidence_item = _dict(item)
            claim = _text(evidence_item.get("claim")) or "Evidence item"
            observed = _text(evidence_item.get("observed"))
            location = _format_location(_dict(evidence_item.get("location")))
            suffix = f" ({location})" if location else ""
            lines.append(f"- {claim}{suffix}" + (f": {observed}" if observed else ""))
        lines.append("")
    fix = _dict(report.get("suggested_fix"))
    _append_section(lines, "Suggested Fix", _text(fix.get("summary")))
    validation = _dict(report.get("validation"))
    commands = [str(item) for item in _list(validation.get("commands")) if str(item)]
    if commands:
        lines.extend(["## Validation", ""])
        for command in commands:
            lines.append(f"- `{command}`")
        lines.append("")
    _append_list(lines, "Limitations", report.get("limitations"))
    _append_list(lines, "How Retrace Found This", report.get("how_retrace_found_this"), numbered=True)
    lines.extend([
        "## Privacy",
        "",
        "- Privacy: Sanitized",
        "- Trace shared: No",
        "- Runtime values: Included, redacted",
        "- Tool transcript: Hidden",
        "",
        "Generated by Retrace - replay-backed debugging for Python.",
        "",
    ])
    return "\n".join(lines)


def render_full_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Full Diagnostic Report",
        "",
        "> This report may contain local paths, runtime values, source snippets, tool calls, and other sensitive debugging information. Share only with trusted recipients.",
        "",
        f"## {_text(report.get('title'))}",
        "",
        f"- Status: {_text(report.get('status'))}",
        f"- Generated: {_text(report.get('created_at'))}",
        "",
    ]
    _append_section(lines, "Summary", _text(report.get("summary")))
    root = _dict(report.get("root_cause"))
    _append_section(lines, "Root Cause", _text(root.get("claim") or root.get("summary")))
    _append_evidence_markdown(lines, report.get("evidence"))
    _append_list(lines, "Replay Walkthrough", report.get("replay_walkthrough"), numbered=True)
    fix = _dict(report.get("suggested_fix"))
    _append_section(lines, "Suggested Fix", _text(fix.get("summary")))
    _append_list(lines, "Open Questions", report.get("open_questions"))
    _append_list(lines, "Limitations", report.get("limitations"))
    transcript = _list(report.get("tool_transcript"))
    if transcript:
        lines.extend(["## Full Tool Transcript", ""])
        for index, action in enumerate(transcript, start=1):
            action_item = _dict(action)
            tool = _text(action_item.get("tool")) or "tool"
            result = _transcript_summary(action_item)
            lines.append(f"{index}. `{tool}`" + (f": {result}" if result else ""))
        lines.append("")
    return "\n".join(lines)


def render_public_html(report: dict[str, Any], *, report_url: str | None = None) -> str:
    title = _text(report.get("title")) or "Retrace Resolution Report"
    body = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="robots" content="noindex,nofollow">',
        f'<meta property="og:title" content="{_h_attr("Retrace found root cause: " + title)}">',
        '<meta property="og:description" content="Generated from replayed runtime evidence, not guesswork.">',
        '<meta property="og:type" content="article">',
        _og_url(report_url),
        _canonical_link(report_url),
        f"<title>{_h(title)} - Retrace</title>",
        _styles(),
        "</head>",
        "<body>",
        '<main class="page">',
        '<header class="header">',
        '<p class="eyebrow">Retrace Resolution Report</p>',
        f"<h1>{_h(title)}</h1>",
        '<p class="proof">Retrace replayed the failed execution and found the runtime evidence behind this bug.</p>',
        _metadata_table([
            ("Status", report.get("status")),
            ("Confidence", _dict(report.get("confidence")).get("level")),
            ("Failure category", _dict(report.get("failure")).get("category")),
            ("Generated", report.get("created_at")),
        ]),
        _report_actions(report_url, github_comment=_github_comment(report, report_url)),
        "</header>",
    ]
    _append_html_section(body, "What happened", report.get("summary"))
    _append_html_section(body, "Root cause", _dict(report.get("root_cause")).get("summary"), class_name="root")
    _append_html_evidence(body, report.get("evidence"))
    _append_html_section(body, "Suggested fix", _dict(report.get("suggested_fix")).get("summary"))
    _append_html_list(body, "Validation", _dict(report.get("validation")).get("commands"), code=True)
    _append_html_list(body, "Limitations", report.get("limitations"))
    _append_html_list(body, "How Retrace found this", report.get("how_retrace_found_this"), ordered=True)
    body.extend([
        '<section class="privacy">',
        "<h2>Privacy</h2>",
        "<p>Anyone with this link can view this public report.</p>",
        "<ul>",
        "<li>Privacy: Sanitized</li>",
        "<li>Trace shared: No</li>",
        "<li>Runtime values: Included, redacted</li>",
        "<li>Tool transcript: Hidden</li>",
        "</ul>",
        "</section>",
        '<footer class="footer">',
        "Generated by Retrace - replay-backed debugging for Python. ",
        '<a href="https://retracesoftware.com">Try Retrace on your Python tests.</a>',
        "</footer>",
        "</main>",
        _copy_script(report_url),
        "</body>",
        "</html>",
    ])
    return "\n".join(body)


def render_full_html(report: dict[str, Any], *, report_url: str | None = None) -> str:
    title = _text(report.get("title")) or "Full Diagnostic Report"
    body = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        '<meta name="robots" content="noindex,nofollow">',
        _canonical_link(report_url),
        f"<title>{_h(title)} - Retrace Diagnostic</title>",
        _styles(),
        "</head>",
        "<body>",
        '<main class="page">',
        '<section class="warning">',
        "<h1>Full diagnostic report</h1>",
        "<p>This report may contain local paths, runtime values, source snippets, tool calls, and other sensitive debugging information. Share only with trusted recipients.</p>",
        "<p>Anyone with this link can view this full diagnostic report.</p>",
        _report_actions(report_url),
        "</section>",
        f"<h2>{_h(title)}</h2>",
        _metadata_table([
            ("Status", report.get("status")),
            ("Generated", report.get("created_at")),
        ]),
    ]
    _append_html_section(body, "Summary", report.get("summary"))
    root = _dict(report.get("root_cause"))
    _append_html_section(body, "Root cause", root.get("claim") or root.get("summary"), class_name="root")
    _append_html_evidence(body, report.get("evidence"))
    _append_html_list(body, "Replay walkthrough", report.get("replay_walkthrough"), ordered=True)
    _append_html_section(body, "Suggested fix", _dict(report.get("suggested_fix")).get("summary"))
    _append_html_list(body, "Open questions", report.get("open_questions"))
    _append_html_list(body, "Limitations", report.get("limitations"))
    transcript = _list(report.get("tool_transcript"))
    if transcript:
        body.extend(['<section class="section">', "<h2>Full tool transcript</h2>", "<ol>"])
        for action in transcript:
            action_item = _dict(action)
            tool = _text(action_item.get("tool")) or "tool"
            result = _transcript_summary(action_item)
            body.append(f"<li><code>{_h(tool)}</code>{': ' + _h(result) if result else ''}</li>")
        body.extend(["</ol>", "</section>"])
    body.extend(["</main>", _copy_script(report_url), "</body>", "</html>"])
    return "\n".join(body)


def render_public_report(artifact: dict[str, Any], *, repo_root: Path | None = None, redaction: str = "standard") -> RenderedReport:
    report = build_public_report(artifact, repo_root=repo_root, redaction=redaction)
    return RenderedReport(
        mode="public",
        report=report,
        markdown=render_public_markdown(report),
        html=render_public_html(report),
    )


def render_full_report(artifact: dict[str, Any]) -> RenderedReport:
    report = build_full_report(artifact)
    return RenderedReport(
        mode="full",
        report=report,
        markdown=render_full_markdown(report),
        html=render_full_html(report),
    )


def write_json(path: Path, report: dict[str, Any]) -> None:
    _write_text(path, json.dumps(report, indent=2, sort_keys=True) + "\n")


def write_markdown(path: Path, markdown: str) -> None:
    _write_text(path, markdown)


def write_html(path: Path, html: str) -> None:
    _write_text(path, html)


def upload_report(
    rendered: RenderedReport,
    *,
    api_key: str,
    endpoint: str = DEFAULT_REPORT_ENDPOINT,
) -> UploadResult:
    payload = {
        "mode": rendered.mode,
        "schema_version": SCHEMA_VERSION,
        "report": rendered.report,
        "html": rendered.html,
        "markdown": rendered.markdown,
        "visibility": "unlisted",
        "redaction_mode": (
            "standard"
            if rendered.mode == "public"
            else "full_diagnostic_with_critical_secret_blocklist"
        ),
    }
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.URLError as exc:
        raise ReportError(f"report upload failed: {exc}") from exc
    try:
        data = json.loads(response_body)
    except json.JSONDecodeError as exc:
        raise ReportError("report upload returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise ReportError("report upload response must be a JSON object")
    url = _string(data.get("url"))
    report_id = _string(data.get("report_id"))
    if not url or not report_id:
        raise ReportError("report upload response missing report_id or url")
    delete_token = _string(data.get("delete_token")) or None
    return UploadResult(report_id=report_id, url=url, delete_token=delete_token)


def contains_critical_secret(value: Any) -> bool:
    for text in _walk_strings(value):
        if _PRIVATE_KEY_RE.search(text):
            return True
        if _BEARER_RE.search(text):
            return True
        if _DB_URL_RE.search(text):
            return True
        if _API_KEY_RE.search(text):
            return True
        if _PASSWORD_RE.search(text):
            return True
    return False


class _Sanitizer:
    def __init__(self, *, repo_root: Path | None, redaction: str) -> None:
        self.repo_root = repo_root.resolve() if repo_root is not None else None
        self.limit = 160 if redaction == "strict" else 500

    def sanitize(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {str(key): self.sanitize(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.sanitize(item) for item in value]
        if isinstance(value, str):
            return self._string(value)
        return value

    def _string(self, value: str) -> str:
        value = self._relativize_paths(value)
        value = _redact_secrets(value)
        if len(value) > self.limit:
            return value[: self.limit - 15] + "... [truncated]"
        return value

    def _relativize_paths(self, value: str) -> str:
        if self.repo_root is None:
            return _ABSOLUTE_PATH_RE.sub(lambda match: _mask_path(match.group("path")), value)

        def replace(match: re.Match[str]) -> str:
            raw_path = match.group("path")
            try:
                path = Path(raw_path).resolve()
                relative = path.relative_to(self.repo_root)
                return str(relative)
            except (OSError, ValueError):
                return _mask_path(raw_path)

        return _ABSOLUTE_PATH_RE.sub(replace, value)


def _source_report(artifact: dict[str, Any]) -> dict[str, Any]:
    report = artifact.get("report")
    if isinstance(report, dict):
        return report
    return artifact


def _location_from(mapping: dict[str, Any]) -> dict[str, Any] | None:
    location = mapping.get("location")
    if isinstance(location, dict):
        return dict(location)
    return None


def _location_from_first_evidence(source: dict[str, Any]) -> dict[str, Any] | None:
    for item in _list(source.get("evidence")):
        location = _location_from(_dict(item))
        if location:
            return location
    return None


def _public_evidence(source: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for raw in _list(source.get("evidence"))[:6]:
        item = _dict(raw)
        items.append({
            "claim": _string(item.get("claim") or item.get("summary")),
            "tool": _string(item.get("tool") or item.get("source")),
            "location": _dict(item.get("location")),
            "observed": _string(item.get("observed") or item.get("result_summary")),
        })
    return items


def _public_walkthrough(source: dict[str, Any], artifact: dict[str, Any]) -> list[str]:
    steps: list[str] = []
    for raw in _list(source.get("replay_walkthrough"))[:8]:
        item = _dict(raw)
        action = _string(item.get("action"))
        finding = _string(item.get("finding"))
        if action and finding:
            steps.append(f"{action}: {finding}")
        elif action:
            steps.append(action)
        elif finding:
            steps.append(finding)
    if steps:
        return steps

    steps = _walkthrough_from_transcript(_list(artifact.get("transcript")))
    if steps:
        return steps

    steps = _walkthrough_from_evidence(_list(source.get("evidence")))
    if steps:
        return steps

    return [
        "Started deterministic replay of the failed execution.",
        "Inspected runtime stack frames and debugger state.",
        "Collected replay evidence for the final report.",
    ]


def _walkthrough_from_transcript(transcript: list[Any]) -> list[str]:
    steps: list[str] = []
    for raw in transcript:
        action = _dict(raw)
        tool = _string(action.get("tool"))
        summary = _transcript_summary(action)
        step = _walkthrough_step(tool, summary)
        if step and step not in steps:
            steps.append(step)
        if len(steps) >= 8:
            break
    return steps


def _walkthrough_from_evidence(evidence: list[Any]) -> list[str]:
    steps: list[str] = []
    for raw in evidence:
        item = _dict(raw)
        source = _string(item.get("tool") or item.get("source"))
        summary = _string(item.get("claim") or item.get("summary"))
        step = _walkthrough_step(source, summary)
        if step and step not in steps:
            steps.append(step)
        if len(steps) >= 8:
            break
    return steps


def _walkthrough_step(tool: str, summary: str) -> str:
    if summary:
        return summary
    if tool == "start_replay_session":
        return "Started deterministic replay of the failed execution."
    if tool == "set_breakpoints":
        return "Set replay breakpoints at the failing test location."
    if tool == "continue_execution":
        return "Continued replay until execution stopped at the target failure."
    if tool == "get_stack_trace":
        return "Read the replay stack trace at the stopped failure."
    if tool == "get_source_context":
        return "Read source context around the stopped frame."
    if tool == "get_variables":
        return "Inspected replay locals at the stopped frame."
    if tool:
        return f"Collected replay evidence with {tool}."
    return ""


def _transcript_summary(action: dict[str, Any]) -> str:
    result_summary = _text(action.get("result_summary"))
    if result_summary:
        return result_summary
    result = _dict(action.get("result"))
    return _text(result.get("summary"))


def _validation_commands(suggested_fix: dict[str, Any]) -> list[str]:
    test = _string(suggested_fix.get("test"))
    return [test] if test else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, sort_keys=True)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _redact_secrets(value: str) -> str:
    value = _PRIVATE_KEY_RE.sub("[redacted_private_key]", value)
    value = _BEARER_RE.sub(r"\1[redacted]", value)
    value = _DB_URL_RE.sub(r"\1[redacted]@", value)
    value = _API_KEY_RE.sub(r"\1[redacted]", value)
    value = _PASSWORD_RE.sub(r"\1[redacted]", value)
    return value


def _mask_path(path: str) -> str:
    parts = [part for part in Path(path).parts if part not in {"/", ""}]
    if len(parts) >= 2:
        return "[path]/" + "/".join(parts[-2:])
    if parts:
        return "[path]/" + parts[-1]
    return "[path]"


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        strings: list[str] = []
        for item in value.values():
            strings.extend(_walk_strings(item))
        return strings
    if isinstance(value, list):
        strings = []
        for item in value:
            strings.extend(_walk_strings(item))
        return strings
    return []


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _h(value: Any) -> str:
    return escape(_text(value), quote=False)


def _h_attr(value: Any) -> str:
    return escape(_text(value), quote=True)


def _canonical_link(report_url: str | None) -> str:
    if not report_url:
        return ""
    return f'<link rel="canonical" href="{_h_attr(report_url)}">'


def _og_url(report_url: str | None) -> str:
    if not report_url:
        return ""
    return f'<meta property="og:url" content="{_h_attr(report_url)}">'


def _report_actions(report_url: str | None, *, github_comment: str | None = None) -> str:
    if not report_url:
        return ""
    actions = [
        '<nav class="actions" aria-label="Report actions">',
        f'<button type="button" class="action" data-copy="{_h_attr(report_url)}">Copy link</button>',
        f'<a class="action" href="{_h_attr(report_url)}.json">Download JSON</a>',
    ]
    if github_comment:
        actions.append(
            f'<button type="button" class="action" data-copy="{_h_attr(github_comment)}">'
            "Copy GitHub comment</button>"
        )
    actions.append("</nav>")
    return "\n".join(actions)


def _github_comment(report: dict[str, Any], report_url: str | None) -> str | None:
    if not report_url:
        return None
    root = _dict(report.get("root_cause"))
    summary = _text(root.get("summary")) or _text(report.get("summary"))
    lines = [
        "### Retrace found the root cause",
        "",
        f"**{_text(report.get('title')) or 'Retrace Resolution Report'}**",
    ]
    if summary:
        lines.extend(["", summary])
    lines.extend(["", f"Report: {report_url}"])
    return "\n".join(lines)


def _copy_script(report_url: str | None) -> str:
    if not report_url:
        return ""
    return """<script>
document.querySelectorAll("[data-copy]").forEach(function (button) {
  button.addEventListener("click", function () {
    var value = button.getAttribute("data-copy") || "";
    var done = function () {
      var original = button.textContent;
      button.textContent = "Copied";
      window.setTimeout(function () { button.textContent = original; }, 1400);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(value).then(done);
      return;
    }
    var textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand("copy");
    document.body.removeChild(textarea);
    done();
  });
});
</script>"""


def _styles() -> str:
    return """<style>
body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #17202a; background: #f7f8fa; }
.page { max-width: 920px; margin: 0 auto; padding: 40px 20px 56px; }
.header, .section, .root, .privacy, .warning { background: #fff; border: 1px solid #d9dee7; border-radius: 8px; padding: 20px; margin: 16px 0; }
.eyebrow { text-transform: uppercase; font-size: 12px; letter-spacing: .08em; color: #526071; font-weight: 700; }
h1, h2 { margin-top: 0; }
.proof { font-size: 18px; color: #334155; }
table { width: 100%; border-collapse: collapse; margin-top: 14px; }
th, td { text-align: left; border-top: 1px solid #e5e9f0; padding: 8px; vertical-align: top; }
.evidence { display: grid; gap: 12px; }
.card { border: 1px solid #d9dee7; border-radius: 8px; padding: 14px; background: #fff; }
.muted { color: #64748b; font-size: 13px; }
.warning { border-color: #d97706; background: #fff8eb; }
.footer { margin-top: 24px; color: #526071; }
code { background: #eef2f7; padding: 1px 4px; border-radius: 4px; }
.actions { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 16px; }
.action { appearance: none; border: 1px solid #b8c1ce; background: #fff; color: #17202a; border-radius: 6px; padding: 8px 11px; font: inherit; font-weight: 650; text-decoration: none; cursor: pointer; }
.action:hover { background: #eef2f7; }
</style>"""


def _metadata_table(items: list[tuple[str, Any]]) -> str:
    rows = ["<table><tbody>"]
    for label, value in items:
        if _text(value):
            rows.append(f"<tr><th>{_h(label)}</th><td>{_h(value)}</td></tr>")
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _append_section(lines: list[str], title: str, body: str) -> None:
    if not body:
        return
    lines.extend([f"## {title}", "", body, ""])


def _append_list(lines: list[str], title: str, values: Any, *, numbered: bool = False) -> None:
    items = _list(values)
    if not items:
        return
    lines.extend([f"## {title}", ""])
    for index, item in enumerate(items, start=1):
        text = _text(item)
        if not text:
            continue
        prefix = f"{index}. " if numbered else "- "
        lines.append(prefix + text)
    lines.append("")


def _append_evidence_markdown(lines: list[str], values: Any) -> None:
    items = _list(values)
    if not items:
        return
    lines.extend(["## Evidence", ""])
    for raw in items:
        item = _dict(raw)
        claim = _text(item.get("claim") or item.get("summary")) or "Evidence item"
        observed = _text(item.get("observed") or item.get("result_summary"))
        location = _format_location(_dict(item.get("location")))
        suffix = f" ({location})" if location else ""
        lines.append(f"- {claim}{suffix}" + (f": {observed}" if observed else ""))
    lines.append("")


def _append_html_section(body: list[str], title: str, content: Any, *, class_name: str = "section") -> None:
    text = _text(content)
    if not text:
        return
    body.extend([
        f'<section class="{class_name}">',
        f"<h2>{_h(title)}</h2>",
        f"<p>{_h(text)}</p>",
        "</section>",
    ])


def _append_html_list(body: list[str], title: str, values: Any, *, ordered: bool = False, code: bool = False) -> None:
    items = [_text(item) for item in _list(values) if _text(item)]
    if not items:
        return
    tag = "ol" if ordered else "ul"
    body.extend(['<section class="section">', f"<h2>{_h(title)}</h2>", f"<{tag}>"])
    for item in items:
        value = f"<code>{_h(item)}</code>" if code else _h(item)
        body.append(f"<li>{value}</li>")
    body.extend([f"</{tag}>", "</section>"])


def _append_html_evidence(body: list[str], values: Any) -> None:
    items = _list(values)
    if not items:
        return
    body.extend(['<section class="section">', "<h2>Evidence</h2>", '<div class="evidence">'])
    for raw in items:
        item = _dict(raw)
        claim = _text(item.get("claim") or item.get("summary")) or "Evidence item"
        observed = _text(item.get("observed") or item.get("result_summary"))
        location = _format_location(_dict(item.get("location")))
        body.extend(['<article class="card">', f"<strong>{_h(claim)}</strong>"])
        if observed:
            body.append(f"<p>{_h(observed)}</p>")
        if location:
            body.append(f'<p class="muted">{_h(location)}</p>')
        body.append("</article>")
    body.extend(["</div>", "</section>"])


def _format_location(location: dict[str, Any]) -> str:
    path = _text(location.get("path"))
    line = _text(location.get("line"))
    function = _text(location.get("function"))
    rendered = path
    if line:
        rendered = f"{rendered}:{line}" if rendered else line
    if function:
        rendered = f"{rendered} ({function})" if rendered else function
    return rendered
