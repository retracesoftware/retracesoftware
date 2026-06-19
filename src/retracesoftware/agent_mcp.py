"""Minimal MCP wrapper for the agent-facing Retrace debugging CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Callable

from retracesoftware.agent_diagnose import diagnose_recording as _diagnose_recording
from retracesoftware.agent_inspect import (
    inspect_expression as _inspect_expression,
    inspect_external_call as _inspect_external_call,
    inspect_external_calls as _inspect_external_calls,
    inspect_failures as _inspect_failures,
    inspect_frame as _inspect_frame,
    inspect_function_code as _inspect_function_code,
    inspect_provenance as _inspect_provenance,
    inspect_recording as _inspect_recording,
    inspect_variable as _inspect_variable,
)


SERVER_NAME = "retrace-agent-debugging"
SERVER_VERSION = "0.1.0"


TOOLS: list[dict[str, Any]] = [
    {
        "name": "retrace_agent_workflow",
        "description": (
            "Use this when you need instructions for how to debug with Retrace MCP tools. "
            "It returns the canonical evidence-first workflow, tool order, and rules for "
            "when an agent may claim root cause."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "retrace_diagnose",
        "description": (
            "Use this first when you want an agentic debugging loop. It inspects the "
            "recording once, summarizes observed evidence, ranks hypotheses, and returns "
            "specific next MCP tool calls to validate or reject those hypotheses. It does "
            "not claim root cause without follow-up evidence."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording": {"type": "string"},
                "max_frames": {"type": "integer", "default": 5},
                "max_vars": {"type": "integer", "default": 12},
                "repr_budget": {"type": "integer", "default": 300},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "retrace_inspect",
        "description": (
            "Use this first when a Retrace recording is available. It reports where the "
            "recorded execution stopped, the failure/exception state, and candidate "
            "application frames. It does not infer root cause."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording": {"type": "string"},
                "max_frames": {"type": "integer", "default": 5},
                "max_vars": {"type": "integer", "default": 50},
                "repr_budget": {"type": "integer", "default": 300},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "retrace_failures",
        "description": (
            "Search replay for raised exception candidates and return cursors, locations, "
            "classification, and exception summaries. Use this when you want the app or "
            "agent to filter stdlib/site-packages/bootstrap noise instead of relying on "
            "the backend to choose the first failure."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording": {"type": "string"},
                "limit": {"type": "integer", "default": 5000},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "retrace_frame",
        "description": (
            "Use this after retrace_inspect to inspect the locals in a chosen application "
            "frame. If frame 0 is a test/assertion/helper frame, inspect the next "
            "business-logic frame."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording": {"type": "string"},
                "frame": {"type": "integer"},
                "repr_budget": {"type": "integer", "default": 300},
            },
            "required": ["frame"],
            "additionalProperties": False,
        },
    },
    {
        "name": "retrace_function_code",
        "description": (
            "Use this after retrace_diagnose or retrace_inspect to fetch the source code "
            "for the function containing a selected application frame. It is frame-scoped "
            "and returns start_line, end_line, current_line, source text, truncation, and "
            "source availability metadata."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording": {"type": "string"},
                "frame": {"type": "integer"},
                "max_chars": {"type": "integer", "default": 12000},
            },
            "required": ["frame"],
            "additionalProperties": False,
        },
    },
    {
        "name": "retrace_eval",
        "description": (
            "Use this after retrace_function_code to evaluate a read-only expression in "
            "a selected application frame, such as a variable, attribute, item lookup, "
            "or assertion sub-expression. It returns a bounded value preview, type, "
            "truncation metadata, and availability reason."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording": {"type": "string"},
                "frame": {"type": "integer"},
                "expression": {"type": "string"},
                "repr_budget": {"type": "integer", "default": 1200},
            },
            "required": ["frame", "expression"],
            "additionalProperties": False,
        },
    },
    {
        "name": "retrace_var",
        "description": (
            "Use this to inspect one named local variable from a selected frame. This is "
            "useful after retrace_frame identifies a local that appears central to the "
            "failure."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording": {"type": "string"},
                "frame": {"type": "integer"},
                "name": {"type": "string"},
                "repr_budget": {"type": "integer", "default": 300},
            },
            "required": ["frame", "name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "retrace_provenance",
        "description": (
            "Use this to ask where a named local entered the selected frame. This reports "
            "stack provenance only. It does not provide full value-level lineage, "
            "mutation history, or container insertion history."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording": {"type": "string"},
                "frame": {"type": "integer"},
                "name": {"type": "string"},
            },
            "required": ["frame", "name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "retrace_external_calls",
        "description": (
            "Use retrace_external_calls after retrace_inspect when the failure may depend on "
            "a DB/API/file/config/time/random result. It returns bounded previews of recorded "
            "external calls before the failure. It does not infer root cause or provide full "
            "value lineage."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording": {"type": "string"},
                "before_failure": {"type": "boolean", "default": True},
                "limit": {"type": "integer", "default": 20},
                "repr_budget": {"type": "integer", "default": 300},
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "retrace_external_call",
        "description": (
            "Use retrace_external_call after retrace_external_calls to expand one selected "
            "recorded DB/API/file/config/time/random call with a larger bounded preview. "
            "It does not infer root cause or provide full value lineage."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording": {"type": "string"},
                "index": {"type": "integer"},
                "before_failure": {"type": "boolean", "default": True},
                "repr_budget": {"type": "integer", "default": 4000},
            },
            "required": ["index"],
            "additionalProperties": False,
        },
    },
]


def list_tools() -> list[dict[str, Any]]:
    """Return MCP tool definitions."""
    return json.loads(json.dumps(TOOLS))


def retrace_inspect(
    recording: str,
    max_frames: int = 5,
    max_vars: int = 50,
    repr_budget: int = 300,
) -> dict[str, Any]:
    """Return observed stop/failure state for a Retrace recording."""
    return _inspect_recording(
        recording,
        max_frames=max_frames,
        max_vars=max_vars,
        repr_budget=repr_budget,
    )


def retrace_agent_workflow() -> dict[str, Any]:
    """Return the canonical agent workflow for Retrace MCP debugging."""
    return {
        "kind": "retrace_agent_workflow",
        "goal": "Move from recorded failure evidence to an evidence-backed root-cause report.",
        "default_sequence": [
            {
                "step": "diagnose",
                "tool": "retrace_diagnose",
                "why": "Observe failure state, ranked hypotheses, and concrete next tool calls.",
            },
            {
                "step": "failure search",
                "tool": "retrace_failures",
                "why": "List raised exception candidates with cursors so the app/agent can filter library or bootstrap noise.",
            },
            {
                "step": "frame",
                "tool": "retrace_frame",
                "why": "Inspect locals in the highest-confidence application frame.",
            },
            {
                "step": "source",
                "tool": "retrace_function_code",
                "why": "Read the containing function before deciding which expression or value to chase.",
            },
            {
                "step": "expression",
                "tool": "retrace_eval",
                "why": "Evaluate read-only variables, attributes, item lookups, or assertion sub-expressions from the source.",
            },
            {
                "step": "local",
                "tool": "retrace_var",
                "why": "Get a bounded preview for a named local that appears central to the failure.",
            },
            {
                "step": "provenance",
                "tool": "retrace_provenance",
                "why": "Ask where a suspicious local entered the selected frame.",
            },
            {
                "step": "external calls",
                "tool": "retrace_external_calls",
                "why": "Check recorded I/O, time, random, config, database, API, or filesystem results before the failure.",
            },
        ],
        "rules": [
            "Do not claim root cause from stack or exception text alone.",
            "Use retrace_failures when the first stop may be noisy; rank candidates in the app/agent before inspecting one.",
            "Inspect function code before choosing expression-level follow-up queries.",
            "Prefer read-only expressions for retrace_eval.",
            "If frame 0 is a test, assertion helper, or framework wrapper, inspect the next business-logic frame.",
            "Treat hypotheses from retrace_diagnose as prompts for evidence gathering, not proof.",
            "Tie every root-cause claim to concrete outputs from frame, source, eval, variable, provenance, or external-call tools.",
        ],
        "root_cause_report_schema": {
            "root_cause": "Concise explanation of the failure cause.",
            "evidence": [
                {"tool": "retrace_function_code", "frame": 0, "lines": [0]},
                {"tool": "retrace_eval", "frame": 0, "expression": "name", "value_preview": "..."},
            ],
            "suggested_fix": "Smallest code or test change supported by the evidence.",
            "confidence": "low|medium|high",
            "open_questions": ["Evidence still missing before confidence can increase."],
        },
    }


def retrace_diagnose(
    recording: str,
    max_frames: int = 5,
    max_vars: int = 12,
    repr_budget: int = 300,
) -> dict[str, Any]:
    """Return an evidence-driven diagnosis loop for a Retrace recording."""
    return _diagnose_recording(
        recording,
        max_frames=max_frames,
        max_vars=max_vars,
        repr_budget=repr_budget,
    )


def retrace_frame(
    recording: str,
    frame: int,
    repr_budget: int = 300,
) -> dict[str, Any]:
    """Return bounded locals for one application frame."""
    return _inspect_frame(
        recording,
        frame_index=frame,
        repr_budget=repr_budget,
    )


def retrace_failures(
    recording: str,
    limit: int = 5000,
) -> dict[str, Any]:
    """Return raised exception candidates with cursors for later inspection."""
    return _inspect_failures(recording, limit=limit)


def retrace_function_code(
    recording: str,
    frame: int,
    max_chars: int = 12000,
) -> dict[str, Any]:
    """Return containing function source for one application frame."""
    return _inspect_function_code(
        recording,
        frame_index=frame,
        max_chars=max_chars,
    )


def retrace_eval(
    recording: str,
    frame: int,
    expression: str,
    repr_budget: int = 1200,
) -> dict[str, Any]:
    """Evaluate one expression in a selected application frame."""
    return _inspect_expression(
        recording,
        frame_index=frame,
        expression=expression,
        repr_budget=repr_budget,
    )


def retrace_var(
    recording: str,
    frame: int,
    name: str,
    repr_budget: int = 300,
) -> dict[str, Any]:
    """Return a bounded preview for one named local."""
    return _inspect_variable(
        recording,
        frame_index=frame,
        name=name,
        repr_budget=repr_budget,
    )


def retrace_provenance(
    recording: str,
    frame: int,
    name: str,
) -> dict[str, Any]:
    """Return stack provenance for one named local, when available."""
    return _inspect_provenance(
        recording,
        frame_index=frame,
        name=name,
    )


def retrace_external_calls(
    recording: str,
    before_failure: bool = True,
    limit: int = 20,
    repr_budget: int = 300,
) -> dict[str, Any]:
    """Return bounded recorded external-call results, when available."""
    return _inspect_external_calls(
        recording,
        before_failure=before_failure,
        limit=limit,
        repr_budget=repr_budget,
    )


def retrace_external_call(
    recording: str,
    index: int,
    before_failure: bool = True,
    repr_budget: int = 4000,
) -> dict[str, Any]:
    """Return one expanded recorded external-call result, when available."""
    return _inspect_external_call(
        recording,
        index=index,
        before_failure=before_failure,
        repr_budget=repr_budget,
    )


def _json_result(value: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, indent=2, sort_keys=True),
            }
        ]
    }


def _tool_error(code: str, message: str, recoverable: bool = True) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "recoverable": recoverable}}


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Call one MCP tool and return an MCP content result."""
    dispatch: dict[str, Callable[..., dict[str, Any]]] = {
        "retrace_agent_workflow": retrace_agent_workflow,
        "retrace_diagnose": retrace_diagnose,
        "retrace_inspect": retrace_inspect,
        "retrace_failures": retrace_failures,
        "retrace_frame": retrace_frame,
        "retrace_function_code": retrace_function_code,
        "retrace_eval": retrace_eval,
        "retrace_var": retrace_var,
        "retrace_provenance": retrace_provenance,
        "retrace_external_calls": retrace_external_calls,
        "retrace_external_call": retrace_external_call,
    }
    if name not in dispatch:
        return _json_result(_tool_error("unknown_tool", f"Unknown tool: {name}"))

    try:
        if "recording" not in arguments and os.environ.get("RETRACE_RECORDING"):
            arguments = {**arguments, "recording": os.environ["RETRACE_RECORDING"]}
        result = dispatch[name](**arguments)
    except subprocess.TimeoutExpired as exc:
        result = _tool_error("replay_timeout", f"Retrace replay timed out after {exc.timeout} seconds.")
    except OSError as exc:
        result = _tool_error("replay_unavailable", str(exc))
    except TypeError as exc:
        result = _tool_error("invalid_arguments", str(exc))

    return _json_result(result)


def handle(request: dict[str, Any]) -> dict[str, Any] | None:
    """Handle one JSON-RPC MCP request."""
    method = request.get("method")
    request_id = request.get("id")

    if method == "notifications/initialized":
        return None

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        }

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": list_tools()}}

    if method == "tools/call":
        params = request.get("params") or {}
        result = call_tool(params.get("name", ""), params.get("arguments") or {})
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> int:
    """Run the MCP server over newline-delimited JSON-RPC on stdio."""
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            request = json.loads(line)
            response = handle(request)
            if response is not None:
                print(json.dumps(response, separators=(",", ":")), flush=True)
        except Exception as exc:  # Defensive: keep the MCP process alive.
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(exc)},
            }
            print(json.dumps(response, separators=(",", ":")), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
