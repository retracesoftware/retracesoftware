"""DAP protocol message construction helpers.

Provides thin wrappers to build well-formed DAP requests, responses, and
events as plain dicts.  No exhaustive dataclass hierarchy — the DAP spec is
large and we only need the subset retracesoftware uses.
"""

from __future__ import annotations

from typing import Any

_seq = 0


def _next_seq() -> int:
    global _seq
    _seq += 1
    return _seq


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------

def response(request: dict, body: dict[str, Any] | None = None, **kwargs) -> dict:
    """Build a success response to *request*."""
    msg: dict[str, Any] = {
        "seq": _next_seq(),
        "type": "response",
        "request_seq": request["seq"],
        "command": request["command"],
        "success": True,
    }
    if body is not None:
        msg["body"] = body
    msg["body"] = {**(msg.get("body") or {}), **kwargs}
    if not msg["body"]:
        del msg["body"]
    return msg


def error_response(request: dict, message: str, error_id: int = 1) -> dict:
    """Build an error response to *request*."""
    return {
        "seq": _next_seq(),
        "type": "response",
        "request_seq": request["seq"],
        "command": request["command"],
        "success": False,
        "message": message,
        "body": {
            "error": {"id": error_id, "format": message},
        },
    }


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def event(name: str, body: dict[str, Any] | None = None, **kwargs) -> dict:
    """Build a DAP event."""
    msg: dict[str, Any] = {
        "seq": _next_seq(),
        "type": "event",
        "event": name,
    }
    merged = {**(body or {}), **kwargs}
    if merged:
        msg["body"] = merged
    return msg


def stopped_event(
    reason: str,
    thread_id: int = 0,
    all_threads_stopped: bool = True,
    **kwargs,
) -> dict:
    return event(
        "stopped",
        reason=reason,
        threadId=thread_id,
        allThreadsStopped=all_threads_stopped,
        **kwargs,
    )


def thread_event(reason: str, thread_id: int) -> dict:
    return event("thread", reason=reason, threadId=thread_id)


def output_event(output: str, category: str = "console") -> dict:
    return event("output", output=output, category=category)


def initialized_event() -> dict:
    return event("initialized")


def terminated_event() -> dict:
    return event("terminated")


def exited_event(exit_code: int = 0) -> dict:
    return event("exited", exitCode=exit_code)


# ---------------------------------------------------------------------------
# Capabilities (returned from initialize)
# ---------------------------------------------------------------------------

CAPABILITIES: dict[str, Any] = {
    "supportsConfigurationDoneRequest": True,
    "supportsFunctionBreakpoints": True,
    "supportsConditionalBreakpoints": True,
    "supportsStepBack": False,
    "supportsGotoTargetsRequest": True,
    "supportsRestartRequest": True,
    "supportsExceptionInfoRequest": True,
    "supportsSteppingGranularity": True,
    "exceptionBreakpointFilters": [
        {"filter": "raised", "label": "Raised Exceptions"},
        {"filter": "uncaught", "label": "Uncaught Exceptions", "default": True},
    ],
}
