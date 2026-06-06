"""Agent-facing deterministic inspection for Retrace recordings."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


LIMITATIONS = [
    "This command reports observed replay/debugger state.",
    "It does not infer root cause.",
    "It does not prove full value-level provenance.",
    "It does not show arbitrary object mutation or container insertion history.",
]

PROVENANCE_LIMITATIONS = [
    "This reports stack provenance only.",
    "It indicates when the value entered the selected frame.",
    "It does not prove object creation history.",
    "It does not prove last mutation history.",
    "It does not show container insertion/update history.",
    "It does not infer root cause.",
]

EXTERNAL_CALL_LIMITATIONS = [
    "This reports recorded external-call results only.",
    "It does not infer root cause.",
    "It does not prove full value-level provenance.",
    "Values are bounded previews.",
]


INTERNAL_PATH_PARTS = (
    "/retracesoftware/",
    "/site-packages/_pytest/",
    "/site-packages/pytest/",
    "/pytest/",
    "/pluggy/",
    "/lib/python",
)


def inspect_recording(
    recording: str,
    *,
    pid: str | None = None,
    max_frames: int = 5,
    max_vars: int = 50,
    repr_budget: int = 300,
    python_executable: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Inspect a recording by driving replay through the control protocol."""
    recording_path = Path(recording)
    commands = _inspection_commands(max_frames=max_frames, max_vars=max_vars, repr_budget=repr_budget)
    result = _run_replay_control(
        recording_path,
        commands,
        python_executable=python_executable or sys.executable,
        timeout_seconds=timeout_seconds,
    )
    responses = _parse_control_responses(result.stdout)
    report = _build_report(
        recording_path=recording_path,
        pid=pid,
        responses=responses,
        stderr=result.stderr,
        returncode=result.returncode,
        max_frames=max_frames,
        max_vars=max_vars,
        repr_budget=repr_budget,
    )
    return report


def inspect_frame(
    recording: str,
    *,
    frame_index: int,
    pid: str | None = None,
    max_frames: int = 20,
    max_vars: int = 50,
    repr_budget: int = 300,
    python_executable: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Return bounded locals for one application frame via replay/control."""
    recording_path = Path(recording)
    commands = _frame_commands(
        frame_index=frame_index,
        max_frames=max_frames,
        max_vars=max_vars,
        repr_budget=repr_budget,
    )
    result = _run_replay_control(
        recording_path,
        commands,
        python_executable=python_executable or sys.executable,
        timeout_seconds=timeout_seconds,
    )
    responses = _parse_control_responses(result.stdout)
    return _build_frame_report(
        recording_path=recording_path,
        pid=pid,
        frame_index=frame_index,
        responses=responses,
        stderr=result.stderr,
        returncode=result.returncode,
        max_frames=max_frames,
        max_vars=max_vars,
        repr_budget=repr_budget,
    )


def inspect_variable(
    recording: str,
    *,
    frame_index: int,
    name: str,
    pid: str | None = None,
    max_frames: int = 20,
    repr_budget: int = 300,
    python_executable: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Return a bounded preview for one named local via replay/control."""
    recording_path = Path(recording)
    commands = _var_commands(
        frame_index=frame_index,
        name=name,
        max_frames=max_frames,
        repr_budget=repr_budget,
    )
    result = _run_replay_control(
        recording_path,
        commands,
        python_executable=python_executable or sys.executable,
        timeout_seconds=timeout_seconds,
    )
    responses = _parse_control_responses(result.stdout)
    return _build_var_report(
        recording_path=recording_path,
        pid=pid,
        frame_index=frame_index,
        name=name,
        responses=responses,
        stderr=result.stderr,
        returncode=result.returncode,
        max_frames=max_frames,
        repr_budget=repr_budget,
    )


def inspect_provenance(
    recording: str,
    *,
    frame_index: int,
    name: str,
    pid: str | None = None,
    max_frames: int = 20,
    repr_budget: int = 300,
    python_executable: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Return stack provenance for one named local via replay/control."""
    recording_path = Path(recording)
    commands = _provenance_commands(
        frame_index=frame_index,
        name=name,
        max_frames=max_frames,
        repr_budget=repr_budget,
    )
    result = _run_replay_control(
        recording_path,
        commands,
        python_executable=python_executable or sys.executable,
        timeout_seconds=timeout_seconds,
    )
    responses = _parse_control_responses(result.stdout)
    return _build_provenance_report(
        recording_path=recording_path,
        pid=pid,
        frame_index=frame_index,
        name=name,
        responses=responses,
        stderr=result.stderr,
        returncode=result.returncode,
        max_frames=max_frames,
        repr_budget=repr_budget,
    )


def inspect_external_calls(
    recording: str,
    *,
    before_failure: bool = False,
    pid: str | None = None,
    limit: int = 20,
    repr_budget: int = 300,
    python_executable: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Return bounded recorded external-call results via replay/control."""
    recording_path = Path(recording)
    commands = _external_calls_commands(
        before_failure=before_failure,
        limit=limit,
        repr_budget=repr_budget,
    )
    result = _run_replay_control(
        recording_path,
        commands,
        python_executable=python_executable or sys.executable,
        timeout_seconds=timeout_seconds,
    )
    responses = _parse_control_responses(result.stdout)
    return _build_external_calls_report(
        recording_path=recording_path,
        pid=pid,
        responses=responses,
        stderr=result.stderr,
        returncode=result.returncode,
        limit=limit,
        repr_budget=repr_budget,
    )


def inspect_external_call(
    recording: str,
    *,
    index: int,
    before_failure: bool = False,
    pid: str | None = None,
    repr_budget: int = 4000,
    python_executable: str | None = None,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    """Return one expanded recorded external-call preview via replay/control."""
    recording_path = Path(recording)
    commands = _external_calls_commands(
        before_failure=before_failure,
        limit=max(index + 1, 1),
        repr_budget=repr_budget,
    )
    result = _run_replay_control(
        recording_path,
        commands,
        python_executable=python_executable or sys.executable,
        timeout_seconds=timeout_seconds,
    )
    responses = _parse_control_responses(result.stdout)
    return _build_external_call_report(
        recording_path=recording_path,
        pid=pid,
        index=index,
        responses=responses,
        stderr=result.stderr,
        returncode=result.returncode,
        repr_budget=repr_budget,
    )


def render_markdown(report: dict[str, Any]) -> str:
    session = report["recording"]
    failure = report["failure"]
    exception = report["exception"]
    stack = report["application_stack"]
    locals_ = report["locals"]
    external_calls = report["external_calls"]
    availability = report["availability"]

    lines: list[str] = ["# Retrace inspection", ""]
    lines.extend([
        "Recording:",
        f"  path: {session['path']}",
        f"  pid: {_display(session.get('pid'))}",
        f"  python_version: {_display(session.get('python_version'))}",
        f"  format: {_display(session.get('format'))}",
        f"  thread_id: {_display(session.get('thread_id'))}",
        "",
        "Failure:",
        f"  reason: {_display(failure.get('reason'))}",
        f"  cursor: {_display(failure.get('cursor'))}",
        f"  location: {_format_location(failure.get('location'))}",
        f"  application_frame_confidence: {_display(failure.get('application_frame_confidence'))}",
        f"  cursor_available: {_display(availability.get('cursor_available'))}",
        f"  exception_available: {_display(availability.get('exception_available'))}",
        f"  locals_available: {_display(availability.get('locals_available'))}",
        f"  external_calls_available: {_display(availability.get('external_calls_available'))}",
        "",
        "Exception/assertion:",
        f"  {_format_exception(exception)}",
        f"  assertion_text: {_display(exception.get('assertion_text'))}",
        "",
        "Application stack:",
    ])
    if stack:
        for i, frame in enumerate(stack):
            lines.append(f"  [{i}] {_format_location(frame)}")
    else:
        lines.append("  unavailable")

    lines.append("")
    lines.append("Locals at frame 0:")
    if locals_:
        for variable in locals_:
            suffix = " (truncated)" if variable.get("truncated") else ""
            lines.append(
                f"  {variable.get('name', 'unavailable')} "
                f"({_display(variable.get('type'))}): {_display(variable.get('repr'))}{suffix}"
            )
    else:
        lines.append("  unavailable")

    lines.extend([
        "",
        "External calls:",
        f"  {_display(external_calls.get('status'))}: {_display(external_calls.get('detail'))}",
        "",
        "Limitations:",
    ])
    for item in report["limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_frame_markdown(report: dict[str, Any]) -> str:
    session = report["recording"]
    frame = report["frame"]
    locals_ = report["locals"]
    availability = report["availability"]

    lines: list[str] = ["# Retrace frame inspection", ""]
    lines.extend([
        "Recording:",
        f"  path: {session['path']}",
        f"  pid: {_display(session.get('pid'))}",
        f"  thread_id: {_display(session.get('thread_id'))}",
        "",
        "Frame:",
        f"  index: {_display(frame.get('index'))}",
        f"  available: {_display(frame.get('available'))}",
        f"  location: {_format_location(frame.get('location'))}",
        f"  application_frame_confidence: {_display(report.get('application_frame_confidence'))}",
        f"  cursor_available: {_display(availability.get('cursor_available'))}",
        f"  exception_available: {_display(availability.get('exception_available'))}",
        f"  locals_available: {_display(availability.get('locals_available'))}",
        f"  external_calls_available: {_display(availability.get('external_calls_available'))}",
        "",
        f"Locals at frame {frame.get('index', 'unavailable')}:",
    ])
    if locals_:
        for variable in locals_:
            suffix = " (truncated)" if variable.get("truncated") else ""
            lines.append(
                f"  {variable.get('name', 'unavailable')} "
                f"({_display(variable.get('type'))}): {_display(variable.get('repr'))}{suffix}"
            )
    else:
        lines.append("  unavailable")

    lines.extend(["", "Limitations:"])
    for item in report["limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_variable_markdown(report: dict[str, Any]) -> str:
    session = report["recording"]
    frame = report["frame"]
    variable = report["variable"]
    availability = report["availability"]

    lines: list[str] = ["# Retrace variable inspection", ""]
    lines.extend([
        "Recording:",
        f"  path: {session['path']}",
        f"  pid: {_display(session.get('pid'))}",
        f"  thread_id: {_display(session.get('thread_id'))}",
        "",
        "Frame:",
        f"  index: {_display(frame.get('index'))}",
        f"  available: {_display(frame.get('available'))}",
        f"  location: {_format_location(frame.get('location'))}",
        f"  application_frame_confidence: {_display(report.get('application_frame_confidence'))}",
        "",
        "Variable:",
        f"  name: {_display(variable.get('name'))}",
        f"  type: {_display(variable.get('type'))}",
        f"  value_preview: {_display(variable.get('value_preview'))}",
        f"  truncated: {_display(variable.get('truncated'))}",
        f"  container_size: {_display(variable.get('container_size'))}",
        "",
        "Availability:",
        f"  variable_available: {_display(availability.get('variable_available'))}",
        f"  deep_expansion_available: {_display(availability.get('deep_expansion_available'))}",
        f"  provenance_available: {_display(availability.get('provenance_available'))}",
        f"  cursor_available: {_display(availability.get('cursor_available'))}",
        f"  exception_available: {_display(availability.get('exception_available'))}",
        f"  locals_available: {_display(availability.get('locals_available'))}",
        f"  external_calls_available: {_display(availability.get('external_calls_available'))}",
        "",
        "Limitations:",
    ])
    for item in report["limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_provenance_markdown(report: dict[str, Any]) -> str:
    session = report["recording"]
    frame = report["frame"]
    variable = report["variable"]
    provenance = report["provenance"]
    availability = report["availability"]

    lines: list[str] = ["# Retrace stack provenance", ""]
    lines.extend([
        "Recording:",
        f"  path: {session['path']}",
        f"  pid: {_display(session.get('pid'))}",
        f"  thread_id: {_display(session.get('thread_id'))}",
        "",
        "Frame:",
        f"  index: {_display(frame.get('index'))}",
        f"  available: {_display(frame.get('available'))}",
        f"  file: {_display(frame.get('file'))}",
        f"  line: {_display(frame.get('line'))}",
        f"  function: {_display(frame.get('function'))}",
        f"  application_frame_confidence: {_display(report.get('application_frame_confidence'))}",
        "",
        "Variable:",
        f"  name: {_display(variable.get('name'))}",
        f"  type: {_display(variable.get('type'))}",
        f"  value_preview: {_display(variable.get('value_preview'))}",
        f"  truncated: {_display(variable.get('truncated'))}",
        "",
        "Provenance:",
        f"  available: {_display(provenance.get('available'))}",
        f"  kind: {_display(provenance.get('kind'))}",
        f"  reason: {_display(provenance.get('reason'))}",
        f"  origin_step: {_display(provenance.get('origin_step'))}",
        f"  origin_location: {_format_origin_location(provenance.get('origin_location'))}",
        f"  origin_op: {_display(provenance.get('origin_op'))}",
        f"  via: {_display(provenance.get('via'))}",
        f"  confidence: {_display(provenance.get('confidence'))}",
        "",
        "Availability:",
        f"  variable_available: {_display(availability.get('variable_available'))}",
        f"  provenance_available: {_display(availability.get('provenance_available'))}",
        f"  cursor_available: {_display(availability.get('cursor_available'))}",
        f"  exception_available: {_display(availability.get('exception_available'))}",
        f"  locals_available: {_display(availability.get('locals_available'))}",
        f"  external_calls_available: {_display(availability.get('external_calls_available'))}",
        "",
        "Limitations:",
    ])
    for item in report["limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_external_calls_markdown(report: dict[str, Any]) -> str:
    session = report["recording"]
    failure = report["failure"]
    calls = report["external_calls"]
    availability = report["availability"]

    lines: list[str] = ["# Retrace external calls", ""]
    lines.extend([
        "Recording:",
        f"  path: {session['path']}",
        "",
        "Failure:",
        f"  cursor: {_display(failure.get('cursor'))}",
        f"  available: {_display(failure.get('available'))}",
        f"  failure_cursor_available: {_display(availability.get('failure_cursor_available'))}",
        "",
        "External calls before failure:",
    ])
    if calls:
        for call in calls:
            suffix = " (truncated)" if call.get("truncated") else ""
            lines.extend([
                f"  [{call.get('index', 'unavailable')}] {_display(call.get('kind'))} {_display(call.get('operation'))}",
                f"      returned: {_display(call.get('result_preview'))}{suffix}",
            ])
    else:
        lines.append("  unavailable")

    lines.extend(["", "Limitations:"])
    for item in report["limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_external_call_markdown(report: dict[str, Any]) -> str:
    session = report["recording"]
    failure = report["failure"]
    call = report["external_call"]
    availability = report["availability"]

    lines: list[str] = ["# Retrace external call", ""]
    lines.extend([
        "Recording:",
        f"  path: {session['path']}",
        "",
        "Failure:",
        f"  cursor: {_display(failure.get('cursor'))}",
        f"  available: {_display(failure.get('available'))}",
        f"  failure_cursor_available: {_display(availability.get('failure_cursor_available'))}",
        "",
        "External call:",
        f"  index: {_display(call.get('index'))}",
        f"  available: {_display(report.get('external_call_available'))}",
        f"  kind: {_display(call.get('kind'))}",
        f"  operation: {_display(call.get('operation'))}",
        f"  inputs: {_display(call.get('inputs_preview'))}",
        f"  result: {_display(call.get('result_preview'))}",
        f"  result_type: {_display(call.get('result_type'))}",
        f"  truncated: {_display(call.get('truncated'))}",
        f"  reason: {_display(call.get('reason'))}",
        "",
        "Limitations:",
    ])
    for item in report["limitations"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


def render_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def _inspection_commands(*, max_frames: int, max_vars: int, repr_budget: int) -> list[dict[str, Any]]:
    return [
        {"id": "hello", "command": "hello"},
        {"id": "stop", "command": "stop_at_failure"},
        {"id": "inspect", "command": "inspect"},
        {"id": "stack", "command": "stack", "params": {"max_frames": max(max_frames * 4, max_frames)}},
        {
            "id": "locals",
            "command": "locals",
            "params": {"frame_index": 0, "max_vars": max_vars, "repr_budget": repr_budget},
        },
        {"id": "close", "command": "close"},
    ]


def _frame_commands(*, frame_index: int, max_frames: int, max_vars: int, repr_budget: int) -> list[dict[str, Any]]:
    return [
        {"id": "hello", "command": "hello"},
        {"id": "stop", "command": "stop_at_failure"},
        {"id": "inspect", "command": "inspect"},
        {"id": "stack", "command": "stack", "params": {"max_frames": max(max_frames * 4, max_frames)}},
        {
            "id": "locals",
            "command": "locals",
            "params": {
                "application_frame_index": frame_index,
                "max_vars": max_vars,
                "repr_budget": repr_budget,
            },
        },
        {"id": "close", "command": "close"},
    ]


def _var_commands(*, frame_index: int, name: str, max_frames: int, repr_budget: int) -> list[dict[str, Any]]:
    return [
        {"id": "hello", "command": "hello"},
        {"id": "stop", "command": "stop_at_failure"},
        {"id": "inspect", "command": "inspect"},
        {"id": "stack", "command": "stack", "params": {"max_frames": max(max_frames * 4, max_frames)}},
        {
            "id": "locals",
            "command": "locals",
            "params": {
                "application_frame_index": frame_index,
                "name": name,
                "max_vars": 1,
                "repr_budget": repr_budget,
            },
        },
        {"id": "close", "command": "close"},
    ]


def _provenance_commands(*, frame_index: int, name: str, max_frames: int, repr_budget: int) -> list[dict[str, Any]]:
    return [
        {"id": "hello", "command": "hello"},
        {"id": "stop", "command": "stop_at_failure"},
        {"id": "inspect", "command": "inspect"},
        {"id": "stack", "command": "stack", "params": {"max_frames": max(max_frames * 4, max_frames)}},
        {
            "id": "locals",
            "command": "locals",
            "params": {
                "application_frame_index": frame_index,
                "name": name,
                "max_vars": 1,
                "repr_budget": repr_budget,
            },
        },
        {
            "id": "provenance",
            "command": "provenance",
            "params": {
                "application_frame_index": frame_index,
                "name": name,
            },
        },
        {"id": "close", "command": "close"},
    ]


def _external_calls_commands(*, before_failure: bool, limit: int, repr_budget: int) -> list[dict[str, Any]]:
    return [
        {"id": "hello", "command": "hello"},
        {"id": "stop", "command": "stop_at_failure"},
        {"id": "inspect", "command": "inspect"},
        {
            "id": "external_calls",
            "command": "external_calls",
            "params": {
                "before_failure": before_failure,
                "limit": limit,
                "repr_budget": repr_budget,
            },
        },
        {"id": "close", "command": "close"},
    ]


def _run_replay_control(
    recording_path: Path,
    commands: Sequence[dict[str, Any]],
    *,
    python_executable: str,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    stdin = "\n".join(json.dumps(command, separators=(",", ":")) for command in commands) + "\n"
    return subprocess.run(
        [
            python_executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording_path),
            "--stdio",
        ],
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
    )


def _parse_control_responses(stdout: str) -> list[dict[str, Any]]:
    responses: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            responses.append(value)
    return responses


def _build_report(
    *,
    recording_path: Path,
    pid: str | None,
    responses: list[dict[str, Any]],
    stderr: str,
    returncode: int,
    max_frames: int,
    max_vars: int,
    repr_budget: int,
) -> dict[str, Any]:
    by_id = {
        str(response.get("id")): response
        for response in responses
        if response.get("ok") is not None and response.get("id") is not None
    }
    stop = next((response for response in responses if response.get("kind") == "stop"), {})
    stop_payload = stop.get("payload", {}) if isinstance(stop.get("payload"), dict) else {}
    inspect_result = _ok_result(by_id.get("inspect"))
    stack_result = _ok_result(by_id.get("stack"))
    locals_result = _ok_result(by_id.get("locals"))
    metadata = _recording_metadata()

    cursor = stop_payload.get("cursor") or inspect_result.get("cursor") or {}
    location = (
        stop_payload.get("location")
        or inspect_result.get("location")
        or _location_from_stack(stack_result)
        or _location_from_cursor(cursor)
    )
    exception = (
        stop_payload.get("exception")
        or inspect_result.get("exception")
        or {"type": "unavailable", "message": "unavailable", "assertion_text": "unavailable"}
    )

    application_stack = _application_frames(stack_result.get("frames", []), max_frames)
    variables = _variables(locals_result.get("variables", []), max_vars=max_vars, repr_budget=repr_budget)
    thread_id = cursor.get("thread_id") if isinstance(cursor, dict) else None
    metadata.update({"path": str(recording_path), "pid": pid, "thread_id": thread_id or "unavailable"})

    failure_reason = stop_payload.get("reason") or inspect_result.get("stop_reason") or "unknown"
    if failure_reason == "eof":
        failure_reason = "end"

    protocol_errors = [
        response
        for response in responses
        if response.get("ok") is False
    ]

    normalized_exception = _normalize_exception(exception)
    cursor_available = isinstance(cursor, dict) and bool(cursor)
    locals_available = bool(variables)
    external_calls_available = False
    exception_available = normalized_exception["type"] != "unavailable"
    application_frame_confidence = stop_payload.get("application_frame_confidence", "low")

    return {
        "recording": metadata,
        "failure": {
            "reason": failure_reason,
            "cursor": cursor or "unavailable",
            "location": location or {},
            "application_frame_confidence": application_frame_confidence,
        },
        "application_frame_confidence": application_frame_confidence,
        "locals_available": locals_available,
        "external_calls_available": external_calls_available,
        "cursor_available": cursor_available,
        "exception_available": exception_available,
        "availability": {
            "application_frame_confidence": application_frame_confidence,
            "locals_available": locals_available,
            "external_calls_available": external_calls_available,
            "cursor_available": cursor_available,
            "exception_available": exception_available,
        },
        "exception": normalized_exception,
        "application_stack": application_stack,
        "locals": variables,
        "external_calls": {
            "status": "unavailable",
            "detail": "not available through the current replay/control inspection surface",
        },
        "control": {
            "returncode": returncode,
            "responses": len(responses),
            "protocol_errors": protocol_errors,
            "stderr_available": bool(stderr.strip()),
            "stderr_preview": "",
        },
        "limitations": LIMITATIONS,
    }


def _build_frame_report(
    *,
    recording_path: Path,
    pid: str | None,
    frame_index: int,
    responses: list[dict[str, Any]],
    stderr: str,
    returncode: int,
    max_frames: int,
    max_vars: int,
    repr_budget: int,
) -> dict[str, Any]:
    by_id = {
        str(response.get("id")): response
        for response in responses
        if response.get("ok") is not None and response.get("id") is not None
    }
    stop = next((response for response in responses if response.get("kind") == "stop"), {})
    stop_payload = stop.get("payload", {}) if isinstance(stop.get("payload"), dict) else {}
    inspect_result = _ok_result(by_id.get("inspect"))
    stack_result = _ok_result(by_id.get("stack"))
    locals_result = _ok_result(by_id.get("locals"))
    metadata = _recording_metadata()

    cursor = stop_payload.get("cursor") or inspect_result.get("cursor") or {}
    thread_id = cursor.get("thread_id") if isinstance(cursor, dict) else None
    metadata.update({"path": str(recording_path), "pid": pid, "thread_id": thread_id or "unavailable"})

    application_stack = _application_frames(stack_result.get("frames", []), max_frames)
    frame_available = 0 <= frame_index < len(application_stack)
    frame_location = application_stack[frame_index] if frame_available else {}
    variables = _variables(locals_result.get("variables", []), max_vars=max_vars, repr_budget=repr_budget)
    normalized_exception = _normalize_exception(
        stop_payload.get("exception")
        or inspect_result.get("exception")
        or {"type": "unavailable", "message": "unavailable", "assertion_text": "unavailable"}
    )
    application_frame_confidence = (
        stop_payload.get("application_frame_confidence", "low") if frame_available else "low"
    )
    protocol_errors = [response for response in responses if response.get("ok") is False]
    locals_available = frame_available and bool(variables)
    cursor_available = isinstance(cursor, dict) and bool(cursor)
    exception_available = normalized_exception["type"] != "unavailable"
    external_calls_available = False

    return {
        "recording": metadata,
        "frame": {
            "index": frame_index,
            "available": frame_available,
            "location": frame_location,
            "status": "available" if frame_available else "unavailable",
        },
        "application_frame_confidence": application_frame_confidence,
        "locals_available": locals_available,
        "external_calls_available": external_calls_available,
        "cursor_available": cursor_available,
        "exception_available": exception_available,
        "availability": {
            "application_frame_confidence": application_frame_confidence,
            "locals_available": locals_available,
            "external_calls_available": external_calls_available,
            "cursor_available": cursor_available,
            "exception_available": exception_available,
        },
        "exception": normalized_exception,
        "locals": variables if frame_available else [],
        "control": {
            "returncode": returncode,
            "responses": len(responses),
            "protocol_errors": protocol_errors,
            "stderr_available": bool(stderr.strip()),
            "stderr_preview": "",
        },
        "limitations": LIMITATIONS,
    }


def _build_var_report(
    *,
    recording_path: Path,
    pid: str | None,
    frame_index: int,
    name: str,
    responses: list[dict[str, Any]],
    stderr: str,
    returncode: int,
    max_frames: int,
    repr_budget: int,
) -> dict[str, Any]:
    by_id = {
        str(response.get("id")): response
        for response in responses
        if response.get("ok") is not None and response.get("id") is not None
    }
    stop = next((response for response in responses if response.get("kind") == "stop"), {})
    stop_payload = stop.get("payload", {}) if isinstance(stop.get("payload"), dict) else {}
    inspect_result = _ok_result(by_id.get("inspect"))
    stack_result = _ok_result(by_id.get("stack"))
    locals_result = _ok_result(by_id.get("locals"))
    metadata = _recording_metadata()

    cursor = stop_payload.get("cursor") or inspect_result.get("cursor") or {}
    thread_id = cursor.get("thread_id") if isinstance(cursor, dict) else None
    metadata.update({"path": str(recording_path), "pid": pid, "thread_id": thread_id or "unavailable"})

    application_stack = _application_frames(stack_result.get("frames", []), max_frames)
    frame_available = 0 <= frame_index < len(application_stack)
    frame_location = application_stack[frame_index] if frame_available else {}
    variables = _variables(locals_result.get("variables", []), max_vars=1, repr_budget=repr_budget)
    observed = variables[0] if frame_available and variables else {}
    variable_available = bool(observed) and observed.get("name") == name
    normalized_exception = _normalize_exception(
        stop_payload.get("exception")
        or inspect_result.get("exception")
        or {"type": "unavailable", "message": "unavailable", "assertion_text": "unavailable"}
    )
    application_frame_confidence = (
        stop_payload.get("application_frame_confidence", "low") if frame_available else "low"
    )
    protocol_errors = [response for response in responses if response.get("ok") is False]
    cursor_available = isinstance(cursor, dict) and bool(cursor)
    exception_available = normalized_exception["type"] != "unavailable"
    external_calls_available = False
    locals_available = frame_available and variable_available

    if variable_available:
        variable = {
            "name": name,
            "type": observed.get("type", "unavailable"),
            "value_preview": observed.get("repr", "unavailable"),
            "truncated": bool(observed.get("truncated")),
            "container_size": observed.get("container_size"),
        }
    else:
        variable = {
            "name": name,
            "type": "unavailable",
            "value_preview": "unavailable",
            "truncated": False,
            "container_size": None,
        }

    availability = {
        "variable_available": variable_available,
        "deep_expansion_available": False,
        "provenance_available": False,
        "application_frame_confidence": application_frame_confidence,
        "locals_available": locals_available,
        "external_calls_available": external_calls_available,
        "cursor_available": cursor_available,
        "exception_available": exception_available,
    }

    return {
        "recording": metadata,
        "frame": {
            "index": frame_index,
            "available": frame_available,
            "location": frame_location,
            "status": "available" if frame_available else "unavailable",
        },
        "variable": variable,
        "application_frame_confidence": application_frame_confidence,
        "locals_available": locals_available,
        "external_calls_available": external_calls_available,
        "cursor_available": cursor_available,
        "exception_available": exception_available,
        "variable_available": variable_available,
        "deep_expansion_available": False,
        "provenance_available": False,
        "availability": availability,
        "exception": normalized_exception,
        "control": {
            "returncode": returncode,
            "responses": len(responses),
            "protocol_errors": protocol_errors,
            "stderr_available": bool(stderr.strip()),
            "stderr_preview": "",
        },
        "limitations": LIMITATIONS,
    }


def _build_provenance_report(
    *,
    recording_path: Path,
    pid: str | None,
    frame_index: int,
    name: str,
    responses: list[dict[str, Any]],
    stderr: str,
    returncode: int,
    max_frames: int,
    repr_budget: int,
) -> dict[str, Any]:
    by_id = {
        str(response.get("id")): response
        for response in responses
        if response.get("ok") is not None and response.get("id") is not None
    }
    stop = next((response for response in responses if response.get("kind") == "stop"), {})
    stop_payload = stop.get("payload", {}) if isinstance(stop.get("payload"), dict) else {}
    inspect_result = _ok_result(by_id.get("inspect"))
    stack_result = _ok_result(by_id.get("stack"))
    locals_result = _ok_result(by_id.get("locals"))
    provenance_result = _ok_result(by_id.get("provenance"))
    metadata = _recording_metadata()

    cursor = stop_payload.get("cursor") or inspect_result.get("cursor") or {}
    thread_id = cursor.get("thread_id") if isinstance(cursor, dict) else None
    metadata.update({"path": str(recording_path), "pid": pid, "thread_id": thread_id or "unavailable"})

    application_stack = _application_frames(stack_result.get("frames", []), max_frames)
    frame_available = 0 <= frame_index < len(application_stack)
    frame_location = application_stack[frame_index] if frame_available else {}
    variables = _variables(locals_result.get("variables", []), max_vars=1, repr_budget=repr_budget)
    observed = variables[0] if frame_available and variables else {}
    variable_available = bool(observed) and observed.get("name") == name
    normalized_exception = _normalize_exception(
        stop_payload.get("exception")
        or inspect_result.get("exception")
        or {"type": "unavailable", "message": "unavailable", "assertion_text": "unavailable"}
    )
    application_frame_confidence = (
        stop_payload.get("application_frame_confidence", "low") if frame_available else "low"
    )
    protocol_errors = [response for response in responses if response.get("ok") is False]
    cursor_available = isinstance(cursor, dict) and bool(cursor)
    exception_available = normalized_exception["type"] != "unavailable"
    external_calls_available = False
    locals_available = frame_available and variable_available
    provenance_available = (
        frame_available
        and variable_available
        and provenance_result.get("available") is True
        and provenance_result.get("kind") == "stack_provenance"
    )

    if variable_available:
        variable = {
            "name": name,
            "type": observed.get("type", "unavailable"),
            "value_preview": observed.get("repr", "unavailable"),
            "truncated": bool(observed.get("truncated")),
        }
    else:
        variable = {
            "name": name,
            "type": "unavailable",
            "value_preview": "unavailable",
            "truncated": False,
        }

    if provenance_available:
        provenance = {
            "available": True,
            "kind": "stack_provenance",
            "origin_step": provenance_result.get("origin_step"),
            "origin_location": _origin_location(provenance_result.get("origin_location")),
            "origin_op": provenance_result.get("origin_op"),
            "via": provenance_result.get("via"),
            "confidence": provenance_result.get("confidence") or "medium",
            "reason": None,
        }
    else:
        provenance = {
            "available": False,
            "kind": "stack_provenance",
            "origin_step": None,
            "origin_location": {},
            "origin_op": None,
            "via": None,
            "confidence": "low",
            "reason": "stack provenance unavailable",
        }

    availability = {
        "variable_available": variable_available,
        "provenance_available": provenance_available,
        "deep_expansion_available": False,
        "application_frame_confidence": application_frame_confidence,
        "locals_available": locals_available,
        "external_calls_available": external_calls_available,
        "cursor_available": cursor_available,
        "exception_available": exception_available,
    }

    return {
        "recording": metadata,
        "frame": {
            "index": frame_index,
            "available": frame_available,
            "file": frame_location.get("filename", "unavailable") if frame_available else "unavailable",
            "line": frame_location.get("line", "unavailable") if frame_available else "unavailable",
            "function": frame_location.get("function", "unavailable") if frame_available else "unavailable",
            "status": "available" if frame_available else "unavailable",
        },
        "variable": variable,
        "provenance": provenance,
        "application_frame_confidence": application_frame_confidence,
        "locals_available": locals_available,
        "external_calls_available": external_calls_available,
        "cursor_available": cursor_available,
        "exception_available": exception_available,
        "variable_available": variable_available,
        "provenance_available": provenance_available,
        "deep_expansion_available": False,
        "availability": availability,
        "exception": normalized_exception,
        "control": {
            "returncode": returncode,
            "responses": len(responses),
            "protocol_errors": protocol_errors,
            "stderr_available": bool(stderr.strip()),
            "stderr_preview": "",
        },
        "limitations": PROVENANCE_LIMITATIONS,
    }


def _build_external_calls_report(
    *,
    recording_path: Path,
    pid: str | None,
    responses: list[dict[str, Any]],
    stderr: str,
    returncode: int,
    limit: int,
    repr_budget: int,
) -> dict[str, Any]:
    by_id = {
        str(response.get("id")): response
        for response in responses
        if response.get("ok") is not None and response.get("id") is not None
    }
    stop = next((response for response in responses if response.get("kind") == "stop"), {})
    stop_payload = stop.get("payload", {}) if isinstance(stop.get("payload"), dict) else {}
    inspect_result = _ok_result(by_id.get("inspect"))
    external_result = _ok_result(by_id.get("external_calls"))
    metadata = _recording_metadata()

    cursor = stop_payload.get("cursor") or inspect_result.get("cursor") or {}
    thread_id = cursor.get("thread_id") if isinstance(cursor, dict) else None
    metadata.update({"path": str(recording_path), "pid": pid, "thread_id": thread_id or "unavailable"})

    calls = _external_calls(external_result.get("calls", []), limit=limit, repr_budget=repr_budget)
    cursor_available = isinstance(cursor, dict) and bool(cursor)
    external_calls_available = bool(calls) and external_result.get("available") is not False
    protocol_errors = [response for response in responses if response.get("ok") is False]

    return {
        "recording": metadata,
        "failure": {
            "cursor": cursor or "unavailable",
            "available": cursor_available,
        },
        "external_calls": calls,
        "external_calls_available": external_calls_available,
        "cursor_available": cursor_available,
        "availability": {
            "external_calls_available": external_calls_available,
            "failure_cursor_available": cursor_available,
        },
        "control": {
            "returncode": returncode,
            "responses": len(responses),
            "protocol_errors": protocol_errors,
            "stderr_available": bool(stderr.strip()),
            "stderr_preview": "",
        },
        "limits": {
            "limit": limit,
            "repr_budget": repr_budget,
        },
        "limitations": EXTERNAL_CALL_LIMITATIONS,
    }


def _build_external_call_report(
    *,
    recording_path: Path,
    pid: str | None,
    index: int,
    responses: list[dict[str, Any]],
    stderr: str,
    returncode: int,
    repr_budget: int,
) -> dict[str, Any]:
    list_report = _build_external_calls_report(
        recording_path=recording_path,
        pid=pid,
        responses=responses,
        stderr=stderr,
        returncode=returncode,
        limit=max(index + 1, 1),
        repr_budget=repr_budget,
    )
    selected = next(
        (call for call in list_report["external_calls"] if call.get("index") == index),
        None,
    )
    external_call_available = selected is not None
    if selected is None:
        selected = {
            "index": index,
            "kind": "unavailable",
            "operation": "unavailable",
            "inputs_preview": "unavailable",
            "result_preview": "unavailable",
            "result_type": "unavailable",
            "truncated": False,
            "container_size": None,
            "occurred_before_failure": True,
            "source": "recorded_external_call",
            "confidence": "low",
            "reason": "external call unavailable",
        }
    else:
        selected = {**selected, "reason": None}

    return {
        "recording": list_report["recording"],
        "failure": list_report["failure"],
        "external_call": selected,
        "external_call_available": external_call_available,
        "external_calls_available": list_report["external_calls_available"],
        "cursor_available": list_report["cursor_available"],
        "availability": {
            "external_call_available": external_call_available,
            "external_calls_available": list_report["external_calls_available"],
            "failure_cursor_available": list_report["availability"]["failure_cursor_available"],
        },
        "control": list_report["control"],
        "limits": {
            "index": index,
            "repr_budget": repr_budget,
        },
        "limitations": EXTERNAL_CALL_LIMITATIONS,
    }


def _recording_metadata() -> dict[str, Any]:
    return {"python_version": "unavailable", "format": "unavailable"}


def _ok_result(response: dict[str, Any] | None) -> dict[str, Any]:
    if not response or response.get("ok") is not True:
        return {}
    result = response.get("result")
    return result if isinstance(result, dict) else {}


def _location_from_stack(stack_result: dict[str, Any]) -> dict[str, Any]:
    frames = stack_result.get("frames")
    if isinstance(frames, list) and frames:
        frame = frames[0]
        if isinstance(frame, dict):
            return {
                "filename": frame.get("filename"),
                "line": frame.get("line"),
                "function": frame.get("function"),
            }
    return {}


def _location_from_cursor(cursor: Any) -> dict[str, Any]:
    if not isinstance(cursor, dict):
        return {}
    return {
        "filename": cursor.get("filename") or "unavailable",
        "line": cursor.get("lineno") or "unavailable",
        "function": cursor.get("function") or "unavailable",
    }


def _origin_location(location: Any) -> dict[str, Any]:
    if not isinstance(location, dict) or not location:
        return {}
    return {
        "file": location.get("file") or location.get("filename") or "unavailable",
        "line": location.get("line", "unavailable"),
        "function": location.get("function") or location.get("name") or "unavailable",
    }


def _application_frames(frames: Any, max_frames: int) -> list[dict[str, Any]]:
    if not isinstance(frames, list):
        return []
    selected = []
    for raw in frames:
        if not isinstance(raw, dict):
            continue
        path = str(raw.get("filename") or raw.get("file") or "")
        if _is_internal_path(path):
            continue
        selected.append({
            "filename": path or "unavailable",
            "line": raw.get("line", "unavailable"),
            "function": raw.get("function") or raw.get("name") or "unavailable",
        })
        if len(selected) >= max_frames:
            break
    return selected


def _is_internal_path(path: str) -> bool:
    normalized = path.replace(os.sep, "/")
    return (
        normalized.startswith("<frozen ")
        or "importlib" in normalized
        or any(part in normalized for part in INTERNAL_PATH_PARTS)
    )


def _variables(variables: Any, *, max_vars: int, repr_budget: int) -> list[dict[str, Any]]:
    if not isinstance(variables, list):
        return []
    result = []
    for raw in variables[:max_vars]:
        if not isinstance(raw, dict):
            continue
        value, truncated_here = _truncate(str(raw.get("value", "unavailable")), repr_budget)
        result.append({
            "name": str(raw.get("name", "unavailable")),
            "type": str(raw.get("type", "unavailable")),
            "repr": value,
            "truncated": bool(raw.get("truncated")) or truncated_here,
            "container_size": raw.get("container_size"),
        })
    return result


def _external_calls(calls: Any, *, limit: int, repr_budget: int) -> list[dict[str, Any]]:
    if not isinstance(calls, list):
        return []
    result = []
    for raw in calls[:limit]:
        if not isinstance(raw, dict):
            continue
        inputs_preview, inputs_truncated = _truncate(str(raw.get("inputs_preview", "unavailable")), repr_budget)
        result_preview, result_truncated = _truncate(str(raw.get("result_preview", "unavailable")), repr_budget)
        result.append({
            "index": raw.get("index", len(result)),
            "kind": str(raw.get("kind", "unknown")),
            "operation": str(raw.get("operation", "unavailable")),
            "inputs_preview": inputs_preview,
            "result_preview": result_preview,
            "result_type": str(raw.get("result_type", "unavailable")),
            "truncated": bool(raw.get("truncated")) or inputs_truncated or result_truncated,
            "container_size": raw.get("container_size"),
            "occurred_before_failure": bool(raw.get("occurred_before_failure", True)),
            "source": str(raw.get("source", "recorded_external_call")),
            "confidence": str(raw.get("confidence", "low")),
        })
    return result


def _normalize_exception(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"type": "unavailable", "message": "unavailable", "assertion_text": "unavailable"}
    return {
        "type": raw.get("type") or raw.get("exceptionId") or "unavailable",
        "message": raw.get("message") or raw.get("description") or "unavailable",
        "assertion_text": raw.get("assertion_text") or "unavailable",
    }


def _truncate(text: str, budget: int) -> tuple[str, bool]:
    if budget <= 0:
        return "", bool(text)
    if len(text) <= budget:
        return text, False
    if budget <= 3:
        return text[:budget], True
    return text[: budget - 3] + "...", True


def _format_location(location: Any) -> str:
    if not isinstance(location, dict) or not location:
        return "unavailable"
    filename = location.get("filename") or location.get("file") or "unavailable"
    line = location.get("line", "unavailable")
    function = location.get("function") or location.get("name") or "unavailable"
    return f"{filename}:{line} in {function}"


def _format_origin_location(location: Any) -> str:
    if not isinstance(location, dict) or not location:
        return "unavailable"
    filename = location.get("file") or location.get("filename") or "unavailable"
    line = location.get("line", "unavailable")
    function = location.get("function") or location.get("name") or "unavailable"
    return f"{filename}:{line} in {function}"


def _format_exception(exception: dict[str, Any]) -> str:
    exc_type = _display(exception.get("type"))
    message = _display(exception.get("message"))
    if exc_type == "unavailable" and message == "unavailable":
        return "unavailable"
    return f"{exc_type}: {message}"


def _display(value: Any) -> str:
    if value is None or value == "":
        return "unavailable"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)
