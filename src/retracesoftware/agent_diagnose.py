"""Evidence-driven diagnosis loop for agent-facing Retrace workflows."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any, Callable

from retracesoftware.agent_inspect import inspect_recording


InspectFn = Callable[..., dict[str, Any]]


def diagnose_recording(
    recording: str,
    *,
    max_frames: int = 5,
    max_vars: int = 12,
    repr_budget: int = 300,
    inspect_fn: InspectFn | None = None,
) -> dict[str, Any]:
    """Inspect a recording once and produce an agent-oriented diagnosis plan."""
    inspect = inspect_recording if inspect_fn is None else inspect_fn
    report = inspect(
        recording,
        max_frames=max_frames,
        max_vars=max_vars,
        repr_budget=repr_budget,
    )
    return build_diagnosis(report, recording=recording)


def build_diagnosis(report: dict[str, Any], *, recording: str | None = None) -> dict[str, Any]:
    """Build a deterministic observe/hypothesize/act/evaluate plan from inspect output."""
    recording_path = _recording_path(report, recording)
    availability = _dict(report.get("availability"))
    exception = _dict(report.get("exception"))
    failure = _dict(report.get("failure"))
    stack = _list_of_dicts(report.get("application_stack"))
    locals_ = _list_of_dicts(report.get("locals"))
    top_frame = stack[0] if stack else {}

    observations = {
        "recording": recording_path,
        "failure_reason": failure.get("reason") or "unavailable",
        "exception": {
            "type": exception.get("type") or "unavailable",
            "message": exception.get("message") or "unavailable",
            "assertion_text": exception.get("assertion_text") or "unavailable",
        },
        "top_application_frame": _frame_summary(top_frame),
        "available_evidence": {
            "cursor": bool(availability.get("cursor_available")),
            "exception": bool(availability.get("exception_available")),
            "locals": bool(availability.get("locals_available")),
            "external_calls": bool(availability.get("external_calls_available")),
        },
    }

    actions = _next_actions(recording_path, availability, stack, locals_, exception)
    hypotheses = _hypotheses(observations, availability, stack, locals_)
    status = "ready_for_agent_review" if actions else "needs_manual_debugger_inspection"

    return {
        "kind": "retrace_agent_diagnosis",
        "status": status,
        "summary": _summary(observations, actions),
        "observations": observations,
        "hypotheses": hypotheses,
        "next_actions": actions,
        "agent_loop": [
            "Observe the failure state from retrace_diagnose or retrace_inspect.",
            "Inspect the highest-confidence application frame before changing code.",
            "Fetch retrace_function_code for that frame before choosing which expression or value to chase.",
            "Evaluate suspicious read-only expressions with retrace_eval.",
            "Expand locals named by assertions, exceptions, or suspicious state.",
            "Inspect external calls when the failure may depend on recorded I/O, time, random, config, database, or network data.",
            "Accept a root cause only when a hypothesis is supported by inspected evidence.",
        ],
        "limitations": [
            "This is deterministic planning, not autonomous LLM reasoning.",
            "Hypotheses are ranked prompts for the next inspection step, not proof.",
            "Do not propose a code fix until the supporting frame, variable, or external-call evidence has been inspected.",
        ],
        "inspect_report": report,
    }


def render_diagnosis_markdown(diagnosis: dict[str, Any]) -> str:
    """Render a diagnosis plan as concise Markdown."""
    observations = _dict(diagnosis.get("observations"))
    exception = _dict(observations.get("exception"))
    frame = _dict(observations.get("top_application_frame"))

    lines = [
        "# Retrace agent diagnosis",
        "",
        f"Status: {_display(diagnosis.get('status'))}",
        "",
        "Observation:",
        f"  recording: {_display(observations.get('recording'))}",
        f"  failure_reason: {_display(observations.get('failure_reason'))}",
        f"  exception: {_display(exception.get('type'))}: {_display(exception.get('message'))}",
        f"  top_frame: {_display(frame.get('file'))}:{_display(frame.get('line'))} in {_display(frame.get('function'))}",
        "",
        "Summary:",
        f"  {_display(diagnosis.get('summary'))}",
        "",
        "Hypotheses:",
    ]

    hypotheses = _list_of_dicts(diagnosis.get("hypotheses"))
    if hypotheses:
        for hypothesis in hypotheses:
            lines.append(
                f"  [{hypothesis.get('id', 'unavailable')}] "
                f"{_display(hypothesis.get('title'))} "
                f"(confidence: {_display(hypothesis.get('confidence'))})"
            )
            lines.append(f"      evidence: {_display(hypothesis.get('evidence'))}")
            lines.append(f"      next: {_display(hypothesis.get('next_action'))}")
    else:
        lines.append("  unavailable")

    lines.extend(["", "Next actions:"])
    actions = _list_of_dicts(diagnosis.get("next_actions"))
    if actions:
        for index, action in enumerate(actions, start=1):
            lines.append(f"  {index}. {_display(action.get('reason'))}")
            lines.append(f"     tool: {_display(action.get('tool'))}")
            lines.append(f"     arguments: {json.dumps(action.get('arguments', {}), sort_keys=True)}")
            if action.get("command"):
                lines.append(f"     command: {action['command']}")
    else:
        lines.append("  Open the recording in the debugger and inspect the application stack manually.")

    lines.extend(["", "Agent loop:"])
    for step in diagnosis.get("agent_loop", []):
        lines.append(f"- {step}")

    lines.extend(["", "Limitations:"])
    for limitation in diagnosis.get("limitations", []):
        lines.append(f"- {limitation}")
    lines.append("")
    return "\n".join(lines)


def render_json(value: dict[str, Any]) -> str:
    return json.dumps(value, indent=2, sort_keys=True) + "\n"


def _recording_path(report: dict[str, Any], recording: str | None) -> str:
    metadata = _dict(report.get("recording"))
    path = metadata.get("path") or recording or ""
    return str(path)


def _next_actions(
    recording: str,
    availability: dict[str, Any],
    stack: list[dict[str, Any]],
    locals_: list[dict[str, Any]],
    exception: dict[str, Any],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if stack:
        actions.append(_action(
            tool="retrace_failures",
            recording=recording,
            arguments={"limit": 5000},
            reason="Search raised exception candidates and let the app/agent filter library or bootstrap noise.",
        ))
        actions.append(_action(
            tool="retrace_frame",
            recording=recording,
            arguments={"frame": 0, "repr_budget": 600},
            reason="Inspect the top application frame locals before forming a fix.",
        ))
        actions.append(_action(
            tool="retrace_function_code",
            recording=recording,
            arguments={"frame": 0, "max_chars": 12000},
            reason="Read the function body for the top application frame to ground variable/provenance reasoning in source.",
        ))

    for variable in _candidate_variables(locals_, exception)[:3]:
        actions.append(_action(
            tool="retrace_eval",
            recording=recording,
            arguments={"frame": 0, "expression": variable, "repr_budget": 1200},
            reason=f"Evaluate expression {variable!r} in the failing frame before tracing provenance.",
        ))
        actions.append(_action(
            tool="retrace_var",
            recording=recording,
            arguments={"frame": 0, "name": variable, "repr_budget": 1200},
            reason=f"Expand local {variable!r}, which appears relevant to the failure text or current frame.",
        ))
        actions.append(_action(
            tool="retrace_provenance",
            recording=recording,
            arguments={"frame": 0, "name": variable},
            reason=f"Ask where local {variable!r} entered the failing frame.",
        ))

    if availability.get("external_calls_available"):
        actions.append(_action(
            tool="retrace_external_calls",
            recording=recording,
            arguments={"before_failure": True, "limit": 20, "repr_budget": 500},
            reason="List recorded external calls before the failure and inspect any value feeding the failing state.",
        ))
    else:
        actions.append(_action(
            tool="retrace_external_calls",
            recording=recording,
            arguments={"before_failure": True, "limit": 20, "repr_budget": 500},
            reason="Check whether the replay backend can expose recorded external calls for this recording.",
        ))

    return actions


def _action(*, tool: str, recording: str, arguments: dict[str, Any], reason: str) -> dict[str, Any]:
    args = {"recording": recording, **arguments}
    command_name = {
        "retrace_frame": "frame",
        "retrace_failures": "failures",
        "retrace_function_code": "function-code",
        "retrace_eval": "eval",
        "retrace_var": "var",
        "retrace_provenance": "provenance",
        "retrace_external_calls": "external-calls",
    }.get(tool, tool)
    command = "retrace mcp --recording " + shlex.quote(recording)
    return {
        "tool": tool,
        "arguments": args,
        "reason": reason,
        "command": command,
        "cli_note": f"Use MCP tool {tool}; CLI shorthand for {command_name} is not available yet.",
    }


def _hypotheses(
    observations: dict[str, Any],
    availability: dict[str, Any],
    stack: list[dict[str, Any]],
    locals_: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    hypotheses: list[dict[str, Any]] = []
    exception = _dict(observations.get("exception"))
    top_frame = _dict(observations.get("top_application_frame"))

    if exception.get("type") != "unavailable":
        hypotheses.append({
            "id": "exception-state",
            "title": "The failing application frame contains the state that explains the exception.",
            "confidence": "medium" if stack else "low",
            "evidence": f"{exception.get('type')}: {exception.get('message')}",
            "next_action": "Inspect frame 0 and expand locals named by the exception or assertion text.",
        })

    if locals_:
        names = ", ".join(str(item.get("name")) for item in locals_[:5])
        hypotheses.append({
            "id": "local-value",
            "title": "A local value at the failure point is unexpected.",
            "confidence": "medium",
            "evidence": f"Frame locals are available: {names}",
            "next_action": "Expand candidate locals and request stack provenance for the strongest one.",
        })

    if availability.get("external_calls_available"):
        hypotheses.append({
            "id": "recorded-boundary",
            "title": "A recorded external result influenced the failing state.",
            "confidence": "medium",
            "evidence": "External-call evidence is available before the failure cursor.",
            "next_action": "List external calls, expand suspicious call results, then connect them to frame locals.",
        })
    else:
        hypotheses.append({
            "id": "missing-boundary-evidence",
            "title": "Root cause cannot yet be tied to an external boundary result.",
            "confidence": "low",
            "evidence": "External-call evidence is unavailable or not yet queried.",
            "next_action": "Query retrace_external_calls before ruling boundary data in or out.",
        })

    if top_frame:
        hypotheses.append({
            "id": "source-location",
            "title": "The root-cause search should start at the top application frame.",
            "confidence": top_frame.get("confidence") or "medium",
            "evidence": _format_frame(top_frame),
            "next_action": "Inspect the frame, then move down the stack only if frame 0 is a wrapper or assertion helper.",
        })

    return hypotheses


def _candidate_variables(locals_: list[dict[str, Any]], exception: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(exception.get(key, ""))
        for key in ("message", "assertion_text")
    ).lower()
    names = [str(item.get("name")) for item in locals_ if item.get("name")]
    scored = sorted(
        names,
        key=lambda name: (0 if name.lower() in text else 1, len(name), name),
    )
    return scored


def _summary(observations: dict[str, Any], actions: list[dict[str, Any]]) -> str:
    exception = _dict(observations.get("exception"))
    frame = _dict(observations.get("top_application_frame"))
    if exception.get("type") != "unavailable" and frame:
        return (
            f"Observed {exception.get('type')} near {_format_frame(frame)}. "
            f"Run {len(actions)} targeted inspection action(s) before proposing a fix."
        )
    if frame:
        return f"Observed a failure near {_format_frame(frame)}. Inspect frame locals next."
    return "The recording was found, but the current backend did not expose enough state for an evidence-backed diagnosis."


def _frame_summary(frame: dict[str, Any]) -> dict[str, Any]:
    if not frame:
        return {"available": False}
    return {
        "available": True,
        "file": frame.get("filename") or frame.get("file") or "unavailable",
        "line": frame.get("line", "unavailable"),
        "function": frame.get("function") or frame.get("name") or "unavailable",
        "confidence": frame.get("confidence") or "medium",
    }


def _format_frame(frame: dict[str, Any]) -> str:
    return f"{frame.get('file', 'unavailable')}:{frame.get('line', 'unavailable')} in {frame.get('function', 'unavailable')}"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _display(value: Any) -> str:
    if value is None or value == "":
        return "unavailable"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)
