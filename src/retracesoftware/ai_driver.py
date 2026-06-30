from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
from typing import Any
from urllib import error, request
from urllib.parse import quote, urljoin
import uuid

from retracesoftware.agent_inspect import _is_internal_path
from retracesoftware.replay import binary_path as replay_binary_path
from retracesoftware.retracepython import DEFAULT_AI_SERVER


DAP_SESSION_ID = "dap-replay-1"
DEFAULT_TIMEOUT = 30.0
MAX_SOURCE_CONTEXT_LINE_CHARS = 4096
AVAILABLE_TOOLS = [
    "start_replay_session",
    "set_breakpoints",
    "continue_execution",
    "reverse_continue",
    "step_over",
    "step_into",
    "step_out",
    "step_back",
    "get_stack_trace",
    "get_exception_info",
    "get_scopes",
    "get_variables",
    "get_source_context",
    "evaluate_expression",
    "stop_replay_session",
]


class DriverError(RuntimeError):
    pass


class DAPRequestError(DriverError):
    """Raised when the Go DAP proxy rejects an inspection or control request."""

    def __init__(
        self,
        message: str,
        *,
        command: str = "",
        category: str = "dap_protocol",
        code: str = "dap_request_failed",
        control_method: str | None = None,
    ) -> None:
        super().__init__(message)
        self.command = command
        self.category = category
        self.code = code
        self.control_method = control_method


class ServiceClient:
    def __init__(self, server: str, api_key: str | None = None) -> None:
        self.server = server.rstrip("/") + "/"
        self.api_key = api_key

    def token(self) -> str:
        if self.api_key:
            return self.api_key
        payload = {
            "client_id": "retracesoftware",
            "install_id": _install_id(),
            "version": _package_version(),
            "capabilities": ["debug_sessions"],
        }
        response = self.post("/v1/client-tokens", payload, authenticated=False)
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise DriverError("Retrace AI service did not return an access token")
        self.api_key = token
        return token

    def post(self, path: str, payload: dict[str, Any], *, authenticated: bool = True) -> dict[str, Any]:
        headers = {
            "content-type": "application/json",
            "user-agent": f"retracesoftware-ai-driver/{_package_version()}",
        }
        if authenticated:
            headers["authorization"] = f"Bearer {self.token()}"
        body = json.dumps(payload).encode("utf-8")
        url = urljoin(self.server, path.lstrip("/"))
        data = b""
        attempts = 1 if "/turn" in path else 3
        for attempt in range(attempts):
            req = request.Request(url, data=body, headers=headers, method="POST")
            try:
                with request.urlopen(req, timeout=60) as resp:
                    data = resp.read()
                break
            except error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")
                if exc.code < 500 or attempt == attempts - 1:
                    raise DriverError(f"Retrace AI service returned HTTP {exc.code}: {detail}") from exc
                time.sleep(0.5 * (attempt + 1))
            except OSError as exc:
                if attempt == attempts - 1:
                    raise DriverError(f"Could not reach Retrace AI service at {url}: {exc}") from exc
                time.sleep(0.5 * (attempt + 1))
        try:
            decoded = json.loads(data.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise DriverError(f"Retrace AI service returned invalid JSON from {url}") from exc
        if not isinstance(decoded, dict):
            raise DriverError(f"Retrace AI service returned non-object JSON from {url}")
        if "error" in decoded:
            raise DriverError(f"Retrace AI service error from {url}: {decoded['error']}")
        return decoded


class DAPSession:
    def __init__(self, replay_bin: str, trace: str) -> None:
        self.replay_bin = replay_bin
        self.trace = str(Path(trace).resolve())
        args = [replay_bin, "--dap", self.trace]
        if Path(self.trace).suffix == ".retrace":
            args = [replay_bin, "--recording", self.trace, "--dap"]
        self.proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert self.proc.stdin is not None
        assert self.proc.stdout is not None
        assert self.proc.stderr is not None
        self.stdin = self.proc.stdin
        self.stdout = self.proc.stdout
        self.stderr = self.proc.stderr
        self.seq = 0
        self.messages: queue.Queue[dict[str, Any] | BaseException | None] = queue.Queue()
        self.pending: list[dict[str, Any]] = []
        self.output = ""
        self.stderr_text = ""
        self.state: dict[str, Any] = {
            "session_id": DAP_SESSION_ID,
            "state": "starting",
            "capabilities": {"dap_bridge": True},
        }
        self.capabilities: dict[str, Any] = {}
        self.frames: list[dict[str, Any]] = []
        self.synthetic_exception: dict[str, str] | None = None
        self.closed = False
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def initialize(self) -> None:
        seq = self.send_request("initialize", {"clientID": "retrace-ai-driver", "adapterID": "retrace"})
        resp = self.wait_for_response(seq)
        self._raise_response_error(resp)
        body = resp.get("body")
        if isinstance(body, dict):
            self.capabilities = body
        self.state["capabilities"] = self._capability_state()
        self.wait_for_event("initialized", timeout=DEFAULT_TIMEOUT, optional=True)

    def launch(self) -> None:
        seq = self.send_request(
            "launch",
            {"type": "retrace", "request": "launch", "recording": self.trace},
        )
        resp = self.wait_for_response(seq)
        self._raise_response_error(resp)

    def probe_exception_breakpoints(self) -> None:
        try:
            seq = self.send_request("setExceptionBreakpoints", {"filters": ["raised"]})
            resp = self.wait_for_response(seq, timeout=5.0)
            self._raise_response_error(resp)
            self.capabilities["exception_breakpoints"] = "raised"
        except Exception:
            self.capabilities["exception_breakpoints"] = "unavailable"
        self.state["capabilities"] = self._capability_state()

    def configuration_done(self) -> None:
        seq = self.send_request("configurationDone", {})
        resp = self.wait_for_response(seq)
        self._raise_response_error(resp)
        event = self.wait_for_event("stopped", "terminated")
        self._apply_stop_event(event)

    def send_request(self, command: str, arguments: dict[str, Any] | None = None) -> int:
        if self.closed:
            raise DriverError("DAP session is closed")
        self.seq += 1
        msg: dict[str, Any] = {
            "seq": self.seq,
            "type": "request",
            "command": command,
        }
        if arguments is not None:
            msg["arguments"] = arguments
        body = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        self.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii"))
        self.stdin.write(body)
        self.stdin.flush()
        return self.seq

    def request(self, command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        seq = self.send_request(command, arguments or {})
        resp = self.wait_for_response(seq)
        self._raise_response_error(resp)
        return resp

    def wait_for_response(self, request_seq: int, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
        return self._wait_for(
            lambda msg: msg.get("type") == "response" and msg.get("request_seq") == request_seq,
            timeout,
        )

    def wait_for_event(
        self,
        *events: str,
        timeout: float = DEFAULT_TIMEOUT,
        optional: bool = False,
    ) -> dict[str, Any]:
        try:
            return self._wait_for(
                lambda msg: msg.get("type") == "event" and msg.get("event") in events,
                timeout,
            )
        except TimeoutError:
            if optional:
                return {}
            raise

    def close(self) -> None:
        if self.closed:
            return
        try:
            seq = self.send_request("disconnect", {})
            self.wait_for_response(seq, timeout=2.0)
        except Exception:
            pass
        self.closed = True
        try:
            self.stdin.close()
        except Exception:
            pass
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        for stream in (self.stdout, self.stderr):
            try:
                stream.close()
            except Exception:
                pass

    def drain_output(self, timeout: float = 0.25) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                item = self.messages.get(timeout=max(0.0, deadline - time.monotonic()))
            except queue.Empty:
                return
            if item is None or isinstance(item, BaseException):
                return
            self._observe_message(item)
            if item.get("type") != "event" or item.get("event") != "output":
                self.pending.append(item)

    def _wait_for(self, predicate, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            for idx, msg in enumerate(self.pending):
                if predicate(msg):
                    return self.pending.pop(idx)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(self._timeout_message())
            item = self.messages.get(timeout=remaining)
            if item is None:
                raise DriverError(self._stream_closed_message())
            if isinstance(item, BaseException):
                raise item
            self._observe_message(item)
            if predicate(item):
                return item
            self.pending.append(item)

    def _read_stdout(self) -> None:
        try:
            while True:
                msg = _read_dap_message(self.stdout)
                if msg is None:
                    self.messages.put(None)
                    return
                self.messages.put(msg)
        except BaseException as exc:
            self.messages.put(exc)

    def _read_stderr(self) -> None:
        chunks: list[str] = []
        while True:
            chunk = self.stderr.readline()
            if not chunk:
                break
            chunks.append(chunk.decode("utf-8", "replace"))
            self.stderr_text = "".join(chunks)

    def _observe_message(self, msg: dict[str, Any]) -> None:
        if msg.get("type") != "event" or msg.get("event") != "output":
            return
        body = msg.get("body")
        if isinstance(body, dict) and isinstance(body.get("output"), str):
            self.output += body["output"]

    def _apply_stop_event(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        if event == "terminated":
            self.state["state"] = "terminated"
            self.state["last_stop"] = {
                "reason": "terminated",
                "thread_id": 1,
                "description": "Replay session terminated.",
            }
        elif event == "stopped":
            body = msg.get("body") if isinstance(msg.get("body"), dict) else {}
            thread_id = int(body.get("threadId") or body.get("thread_id") or 1)
            self.state["state"] = "stopped"
            self.state["last_stop"] = {
                "reason": body.get("reason") or "",
                "thread_id": thread_id,
                "description": body.get("description") or body.get("text") or "",
            }
        self.state["session_id"] = DAP_SESSION_ID
        self.state["capabilities"] = self._capability_state()

    def _capability_state(self) -> dict[str, Any]:
        state = {
            "dap_bridge": True,
            "conditional_breakpoints": bool(self.capabilities.get("supportsConditionalBreakpoints")),
            "step_back": bool(self.capabilities.get("supportsStepBack")),
            "reverse_continue": True,
            "evaluate": "unavailable",
            "source_context": True,
            "stack_trace": True,
            "locals": True,
        }
        if "exception_breakpoints" in self.capabilities:
            state["exception_breakpoints"] = self.capabilities["exception_breakpoints"]
        return state

    def _raise_response_error(self, resp: dict[str, Any]) -> None:
        if resp.get("success", False):
            return
        raise _parse_dap_error_response(resp, self.output, self.stderr_text)

    def _timeout_message(self) -> str:
        details = ["DAP request timed out"]
        if self.output.strip():
            details.append("dap output: " + _tail(self.output.strip(), 1000))
        if self.stderr_text.strip():
            details.append("stderr: " + _tail(self.stderr_text.strip(), 1000))
        return "; ".join(details)

    def _stream_closed_message(self) -> str:
        if self.output.strip():
            return "DAP stream closed: " + _tail(self.output.strip(), 1000)
        if self.stderr_text.strip():
            return "DAP stream closed: " + _tail(self.stderr_text.strip(), 1000)
        rc = self.proc.poll()
        if rc is not None:
            return f"DAP stream closed with exit {rc}"
        return "DAP stream closed"


class DAPExecutor:
    def __init__(self, trace: str, replay_bin: str | None = None) -> None:
        if not trace:
            raise DriverError("dap executor requires --trace pointing at a recording")
        self.trace = str(Path(trace).resolve())
        self.replay_bin = replay_bin or replay_binary_path()
        self.session: DAPSession | None = None

    def available_tools(self) -> list[str]:
        return list(AVAILABLE_TOOLS)

    def close(self) -> None:
        if self.session is not None:
            self.session.close()
            self.session = None

    def execute(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        arguments = arguments or {}
        try:
            if name == "start_replay_session":
                return self.start_replay_session(arguments)
            if name == "set_breakpoints":
                return self.set_breakpoints(arguments)
            if name == "set_exception_breakpoints":
                return self.set_exception_breakpoints(arguments)
            if name in {
                "continue_execution",
                "reverse_continue",
                "step_over",
                "step_into",
                "step_out",
                "step_back",
            }:
                dap_command = {
                    "continue_execution": "continue",
                    "reverse_continue": "reverseContinue",
                    "step_over": "next",
                    "step_into": "stepIn",
                    "step_out": "stepOut",
                    "step_back": "stepBack",
                }[name]
                return self.navigate(name, dap_command, arguments)
            if name == "get_stack_trace":
                return self.get_stack_trace(arguments)
            if name == "get_exception_info":
                return self.get_exception_info(arguments)
            if name == "get_scopes":
                return self.get_scopes(arguments)
            if name == "get_variables":
                return self.get_variables(arguments)
            if name == "get_source_context":
                return self.get_source_context(arguments)
            if name == "evaluate_expression":
                return self.evaluate_expression(arguments)
            if name == "stop_replay_session":
                return self.stop_replay_session()
            return _tool_error(name, "tool_unavailable", f"{name} is not available")
        except Exception as exc:
            return _dap_error(name, exc, self.session.state if self.session else None)

    def start_replay_session(self, arguments: dict[str, Any]) -> dict[str, Any]:
        trace = self.trace
        arg_trace = arguments.get("trace")
        if isinstance(arg_trace, dict) and isinstance(arg_trace.get("path"), str):
            trace = arg_trace["path"]
        resolved_trace = str(Path(trace).resolve())
        if (
            self.session is not None
            and not self.session.closed
            and self.session.trace == resolved_trace
            and not bool(arguments.get("restart"))
        ):
            return {
                "ok": True,
                "summary": "Retrace DAP replay session is already active.",
                "data": {
                    "session_id": DAP_SESSION_ID,
                    "trace": resolved_trace,
                    "dap": {
                        "enabled": True,
                        "capabilities": self.session.capabilities,
                    },
                    "note": "Pass restart=true to force a fresh replay session.",
                },
                "session": self.session.state,
            }
        self.close()
        session = DAPSession(self.replay_bin, resolved_trace)
        self.session = session
        session.initialize()
        session.launch()
        session.probe_exception_breakpoints()
        session.configuration_done()
        return {
            "ok": True,
            "summary": "Started Retrace DAP replay session and stopped at entry.",
            "data": {
                "session_id": DAP_SESSION_ID,
                "trace": str(Path(trace).resolve()),
                "dap": {
                    "enabled": True,
                    "capabilities": session.capabilities,
                },
                "note": "All replay/debugger operations for this executor are DAP requests to the Retrace Go proxy.",
            },
            "session": session.state,
        }

    def set_breakpoints(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session("set_breakpoints")
        source = arguments.get("source") if isinstance(arguments.get("source"), dict) else {}
        path = source.get("path") if isinstance(source.get("path"), str) else ""
        bps = arguments.get("breakpoints") if isinstance(arguments.get("breakpoints"), list) else []
        breakpoints = []
        lines = []
        for bp in bps:
            if not isinstance(bp, dict) or not isinstance(bp.get("line"), int):
                continue
            item: dict[str, Any] = {"line": bp["line"]}
            if isinstance(bp.get("condition"), str) and bp["condition"]:
                item["condition"] = bp["condition"]
            breakpoints.append(item)
            lines.append(bp["line"])
        if not path or not breakpoints:
            return _tool_error("set_breakpoints", "invalid_tool_arguments", "source.path and breakpoints are required")
        resp = session.request(
            "setBreakpoints",
            {
                "source": {"name": Path(path).name, "path": path},
                "breakpoints": breakpoints,
                "lines": lines,
            },
        )
        data = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        return {"ok": True, "summary": "DAP setBreakpoints completed.", "data": data, "session": session.state}

    def set_exception_breakpoints(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session("set_exception_breakpoints")
        raw_filters = arguments.get("filters")
        filters = [
            item
            for item in raw_filters
            if isinstance(item, str)
        ] if isinstance(raw_filters, list) else []
        resp = session.request("setExceptionBreakpoints", {"filters": filters})
        data = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        session.capabilities["exception_breakpoints"] = ",".join(filters) if filters else "disabled"
        session.state["capabilities"] = session._capability_state()
        summary = (
            "DAP exception breakpoints disabled."
            if not filters
            else f"DAP exception breakpoints set to {', '.join(filters)}."
        )
        return {"ok": True, "summary": summary, "data": data, "session": session.state}

    def navigate(self, tool: str, dap_command: str, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session(tool)
        thread_id = int(arguments.get("thread_id") or 1)
        session.request(dap_command, {"threadId": thread_id})
        event = session.wait_for_event("stopped", "terminated")
        session._apply_stop_event(event)
        if session.state.get("state") == "terminated":
            session.drain_output()
            exception, frames = _parse_python_traceback(session.output)
            if exception is None or not frames:
                exception, frames = self._direct_replay_traceback(session.trace)
            if exception is not None and frames:
                session.synthetic_exception = exception
                session.frames = frames
                session.state["state"] = "stopped"
                session.state["last_stop"] = {
                    "reason": "exception",
                    "thread_id": thread_id,
                    "description": exception.get("message", ""),
                }
        stop = session.state.get("last_stop") if isinstance(session.state.get("last_stop"), dict) else {}
        state = session.state.get("state")
        reason = stop.get("reason") if isinstance(stop, dict) else ""
        if state == "terminated":
            summary = f"{tool} reached DAP terminated state."
        elif reason:
            summary = f"{tool} stopped replay through DAP with reason {reason!r}."
        else:
            summary = f"{tool} completed through DAP."
        return {
            "ok": True,
            "summary": summary,
            "data": {"state": session.state.get("state"), "stop": session.state.get("last_stop")},
            "session": session.state,
        }

    def get_stack_trace(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session("get_stack_trace")
        thread_id = int(arguments.get("thread_id") or 1)
        if session.synthetic_exception is not None and session.frames:
            return self._application_stack_result(
                session,
                session.frames,
                source="dap.output.traceback",
            )

        dap_frames: list[dict[str, Any]] = []
        recovery_note: str | None = None
        try:
            resp = session.request(
                "stackTrace",
                {
                    "threadId": thread_id,
                    "startFrame": int(arguments.get("start_frame") or 0),
                    "levels": int(arguments.get("levels") or 20),
                },
            )
            body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
            dap_frames = _stack_frames(body)
        except DAPRequestError as exc:
            if exc.category != "inspection_unavailable":
                raise
            recovery_note = (
                "Replay stopped at a non-application location that DAP cannot inspect; "
                "recovering application context from recorded failure output."
            )

        application_frames = _application_dap_frames(dap_frames)
        source = "dap.stackTrace"
        if not application_frames:
            exception, fallback_frames = self._recover_application_traceback(session)
            if fallback_frames:
                application_frames = _application_dap_frames(fallback_frames)
                source = "replay.traceback"
                if exception is not None:
                    session.synthetic_exception = exception
                    session.state["state"] = "stopped"
                    session.state["last_stop"] = {
                        "reason": "exception",
                        "thread_id": thread_id,
                        "description": exception.get("message", ""),
                    }
                if recovery_note is None:
                    recovery_note = (
                        "Recovered application stack frames from recorded pytest/failure output."
                    )

        session.frames = application_frames
        if not application_frames:
            return _application_context_unavailable(session, recovery_note)

        return self._application_stack_result(
            session,
            application_frames,
            source=source,
            recovery_note=recovery_note,
        )

    def _application_stack_result(
        self,
        session: DAPSession,
        frames: list[dict[str, Any]],
        *,
        source: str,
        recovery_note: str | None = None,
    ) -> dict[str, Any]:
        data: dict[str, Any] = {
            "stack_frames": frames,
            "total_frames": len(frames),
            "source": source,
        }
        if recovery_note:
            data["recovery_note"] = recovery_note
        return {
            "ok": True,
            "summary": _application_stack_summary(frames),
            "data": data,
            "session": session.state,
        }

    def _recover_application_traceback(
        self,
        session: DAPSession,
    ) -> tuple[dict[str, str] | None, list[dict[str, Any]]]:
        cwd = _recording_cwd_for_trace(session.trace)
        hint = _pytest_failure_hint_from_output(session.output, cwd=cwd)
        if hint is not None:
            exception, frames = _exception_and_frames_from_pytest_hint(hint, session.output)
            application = _application_dap_frames(frames)
            if application:
                return exception, application
            if frames:
                return exception, frames

        exception, frames = _parse_python_traceback(session.output)
        application = _application_dap_frames(frames) if frames else []
        if application:
            return exception, application
        return self._direct_replay_traceback(session.trace)

    def get_exception_info(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session("get_exception_info")
        if session.synthetic_exception is not None:
            data = {
                "exceptionId": session.synthetic_exception.get("type") or "<unknown>",
                "description": session.synthetic_exception.get("message") or "",
                "breakMode": "always",
                "details": {
                    "typeName": session.synthetic_exception.get("type") or "<unknown>",
                    "message": session.synthetic_exception.get("message") or "",
                },
            }
            return {"ok": True, "summary": _exception_summary(data), "data": data, "session": session.state}
        resp = session.request("exceptionInfo", {"threadId": int(arguments.get("thread_id") or 1)})
        data = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        if data.get("exceptionId") in (None, "", "<none>"):
            exception, frames = self._direct_replay_traceback(session.trace)
            if exception is not None and frames:
                session.synthetic_exception = exception
                session.frames = frames
                session.state["state"] = "stopped"
                session.state["last_stop"] = {
                    "reason": "exception",
                    "thread_id": int(arguments.get("thread_id") or 1),
                    "description": exception.get("message", ""),
                }
                data = {
                    "exceptionId": exception.get("type") or "<unknown>",
                    "description": exception.get("message") or "",
                    "breakMode": "always",
                    "details": {
                        "typeName": exception.get("type") or "<unknown>",
                        "message": exception.get("message") or "",
                    },
                }
        return {"ok": True, "summary": _exception_summary(data), "data": data, "session": session.state}

    def get_scopes(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session("get_scopes")
        resp = session.request("scopes", {"frameId": int(arguments.get("frame_id") or 0)})
        body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        scopes = []
        for scope in body.get("scopes") if isinstance(body.get("scopes"), list) else []:
            if not isinstance(scope, dict):
                continue
            scopes.append(
                {
                    "name": str(scope.get("name") or "scope"),
                    "variables_reference": int(scope.get("variablesReference") or 0),
                    "expensive": bool(scope.get("expensive")),
                }
            )
        return {"ok": True, "summary": "Read DAP scopes for the selected frame.", "data": {"scopes": scopes}, "session": session.state}

    def get_variables(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session("get_variables")
        ref = int(arguments.get("variables_reference") or arguments.get("variablesReference") or 1)
        try:
            resp = session.request("variables", {"variablesReference": ref})
        except DAPRequestError as exc:
            if exc.category == "inspection_unavailable":
                return _application_inspection_unavailable(
                    "get_variables",
                    session.state,
                    "Set a source breakpoint on application code, continue, then inspect locals there.",
                )
            raise
        body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        variables = []
        for var in body.get("variables") if isinstance(body.get("variables"), list) else []:
            if not isinstance(var, dict):
                continue
            variables.append(
                {
                    "name": str(var.get("name") or ""),
                    "value": str(var.get("value") or ""),
                    "type": str(var.get("type") or "") if var.get("type") is not None else "",
                    "variables_reference": int(var.get("variablesReference") or 0),
                }
            )
        return {"ok": True, "summary": f"Read {len(variables)} variable(s) through DAP.", "data": {"variables": variables}, "session": session.state}

    def get_source_context(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session("get_source_context")
        path, line = _source_from_arguments(arguments, session.frames)
        if not path:
            return _tool_error("get_source_context", "invalid_tool_arguments", "source.path is required")
        if line <= 0:
            line = 1
        before = int(arguments.get("before") or 8)
        after = int(arguments.get("after") or 8)
        resp = session.request("source", {"source": {"name": Path(path).name, "path": path}})
        body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        content = body.get("content") if isinstance(body.get("content"), str) else ""
        context = _source_window(content, line, before, after)
        return {
            "ok": True,
            "summary": f"Read source context through DAP around {Path(path).name}:{line}.",
            "data": {
                "source": {"path": path, "mime_type": body.get("mimeType") or "text/x-python", "via": "dap.source"},
                "context": context,
            },
            "session": session.state,
        }

    def evaluate_expression(self, arguments: dict[str, Any]) -> dict[str, Any]:
        session = self._require_session("evaluate_expression")
        resp = session.request(
            "evaluate",
            {
                "expression": str(arguments.get("expression") or ""),
                "frameId": int(arguments.get("frame_id") or 0),
                "context": "repl",
            },
        )
        body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        return {
            "ok": True,
            "summary": "Evaluated expression through DAP.",
            "data": {
                "result": str(body.get("result") or ""),
                "type": str(body.get("type") or "") if body.get("type") is not None else "",
                "variables_reference": int(body.get("variablesReference") or 0),
            },
            "session": session.state,
        }

    def stop_replay_session(self) -> dict[str, Any]:
        if self.session is None:
            return {"ok": True, "summary": "No DAP replay session was active.", "data": {"state": "terminated"}, "session": {"state": "terminated"}}
        self.close()
        return {"ok": True, "summary": "DAP replay session closed.", "data": {"state": "terminated"}, "session": {"state": "terminated"}}

    def _require_session(self, tool: str) -> DAPSession:
        if self.session is None or self.session.closed:
            raise DriverError(f"{tool} requires an active DAP replay session")
        return self.session

    def _direct_replay_traceback(self, trace: str) -> tuple[dict[str, str] | None, list[dict[str, Any]]]:
        pidfile = self._pidfile_for_trace(trace)
        if pidfile is None:
            return None, []
        try:
            completed = subprocess.run(
                [self.replay_bin, str(pidfile)],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None, []
        text = (completed.stdout or "") + (completed.stderr or "")
        hint = _pytest_failure_hint_from_output(text, cwd=_recording_cwd_for_trace(trace))
        if hint is not None:
            exception, frames = _exception_and_frames_from_pytest_hint(hint, text)
            application = _application_dap_frames(frames)
            if application:
                return exception, application
            if frames:
                return exception, frames
        exception, frames = _parse_python_traceback(text)
        application = _application_dap_frames(frames) if frames else []
        if application:
            return exception, application
        return None, []

    def _pidfile_for_trace(self, trace: str) -> Path | None:
        path = Path(trace)
        if path.suffix != ".retrace":
            return path
        try:
            subprocess.run(
                [self.replay_bin, "--recording", str(path), "--extract"],
                capture_output=True,
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        index_path = path.with_suffix(".d") / "index.json"
        try:
            decoded = json.loads(index_path.read_text(encoding="utf-8"))
            pid = int(decoded.get("root", {}).get("pid") or 0)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None
        if pid <= 0:
            return None
        return index_path.parent / f"{pid}.bin"


class StubExecutor:
    def available_tools(self) -> list[str]:
        return list(AVAILABLE_TOOLS)

    def close(self) -> None:
        pass

    def execute(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        return _tool_error(name, "replay_provider_unconfigured", "No replay tool executor is configured")


def run_driver(args: argparse.Namespace) -> dict[str, Any]:
    client = ServiceClient(args.server, os.environ.get("RETRACE_API_KEY"))
    executor = _build_executor(args)
    transcript: list[dict[str, Any]] = []
    try:
        initial_observation = _initial_observation(args, executor, transcript)
        initial_session = _executor_session_state(executor)
        start_payload = {
            "task": {
                "goal": "diagnose_failure",
                "investigation_target": args.target,
                "user_request": args.task,
            },
            "trace": str(Path(args.trace).resolve()) if args.trace else "",
            "available_tools": executor.available_tools(),
            "initial_observation": initial_observation,
            "session": initial_session,
            "max_tool_calls": args.max_tool_calls,
        }
        if args.time_budget_ms is not None:
            start_payload["time_budget_ms"] = args.time_budget_ms

        response = client.post("/v1/debug-sessions", start_payload)
        debug_session_id = str(response.get("debug_session_id") or "")
        if not debug_session_id:
            raise DriverError("Retrace AI service did not return debug_session_id")

        for _ in range(args.max_tool_calls + 1):
            action = response.get("response")
            if not isinstance(action, dict):
                raise DriverError("Retrace AI service response is missing response object")
            if action.get("kind") == "final_report":
                return _artifact(args, debug_session_id, action.get("report"), transcript)
            if action.get("kind") != "tool_request":
                raise DriverError(f"Unexpected Retrace AI response kind: {action.get('kind')!r}")
            tool = str(action.get("tool") or "")
            tool_args = action.get("arguments") if isinstance(action.get("arguments"), dict) else {}
            turn_id = response.get("turn_id")
            if not isinstance(turn_id, str) or not turn_id:
                raise DriverError("Retrace AI service tool request is missing turn_id")
            result = _json_clone(executor.execute(tool, tool_args))
            transcript.append({"tool": tool, "arguments": tool_args, "result": result})
            response = client.post(
                f"/v1/debug-sessions/{quote(debug_session_id)}/turn",
                {"turn_id": turn_id, "tool_result": result},
            )
        raise DriverError("Retrace AI service did not produce a final report before the tool budget was exhausted")
    finally:
        executor.close()


def _initial_observation(
    args: argparse.Namespace,
    executor: DAPExecutor | StubExecutor,
    transcript: list[dict[str, Any]],
) -> dict[str, Any]:
    observation: dict[str, Any] = {
        "summary": "Driver initialized. No replay tool has been called yet.",
    }
    if not _task_mentions_pytest(args.task) or not isinstance(executor, DAPExecutor):
        return observation

    hint = _pytest_failure_hint(args.trace)
    if not hint:
        observation["summary"] = (
            "Driver initialized for a pytest failure. No application failure candidate "
            "was available before AI tool selection. Prefer a source breakpoint on the "
            "failing test or application code before inspecting stack/source/locals."
        )
        return observation

    observation["summary"] = (
        "Driver initialized for a pytest failure with an application failure candidate. "
        "The driver will pre-position replay at the application assertion instead of "
        "pytest-internal or exception breakpoints."
    )
    observation["tool_result"] = {
        "pytest_failure_candidate": hint,
        "guidance": (
            "Inspect application frames and locals at the pre-positioned source breakpoint. "
            "Do not restart the replay session unless the current session is unusable."
        ),
    }

    prelude = _prime_pytest_failure_breakpoint(executor, hint)
    transcript.extend(prelude)
    if prelude:
        final = prelude[-1].get("result") if isinstance(prelude[-1].get("result"), dict) else {}
        stack = final.get("data") if isinstance(final.get("data"), dict) else {}
        frames = stack.get("stack_frames") if isinstance(stack.get("stack_frames"), list) else []
        observation["summary"] = (
            "Driver initialized and pre-positioned DAP replay at the application pytest "
            "failure candidate."
        )
        observation["tool_result"] = {
            **observation["tool_result"],
            "prelude": {
                "tool_count": len(prelude),
                "last_result_summary": final.get("summary", ""),
                "application_frame_count": len(frames),
                "session": final.get("session", {}),
            },
        }
    return observation


def _task_mentions_pytest(task: str) -> bool:
    return bool(re.search(r"(^|\s|/|\\|-)pytest(\s|$|/|\\)", task))


def _pytest_failure_hint(trace: str, *, replay_bin: str | None = None) -> dict[str, Any] | None:
    if not trace:
        return None
    try:
        timeout = int(os.environ.get("RETRACE_AI_FAILURE_SEARCH_TIMEOUT", "20") or "20")
        limit = int(os.environ.get("RETRACE_AI_FAILURE_SEARCH_LIMIT", "5000") or "5000")
    except ValueError:
        timeout = 20
        limit = 5000
    if replay_bin is not None:
        control_recording = _control_recording_for_trace(trace, replay_bin=replay_bin)
    else:
        control_recording = _control_recording_for_trace(trace)
    if control_recording is None:
        return None

    output_hint = _pytest_failure_hint_from_replay_output(
        control_recording,
        cwd=_recording_cwd_for_trace(trace),
        timeout=timeout,
        **({"replay_bin": replay_bin} if replay_bin is not None else {}),
    )
    if output_hint is not None and output_hint.get("classification") == "application":
        return output_hint

    inspect_hint: dict[str, Any] | None = None
    try:
        from retracesoftware.agent_inspect import inspect_failures

        report = inspect_failures(str(control_recording), limit=limit, timeout_seconds=timeout)
        inspect_hint = _select_pytest_failure_candidate(report)
        if inspect_hint is not None and inspect_hint.get("classification") == "application":
            return inspect_hint
    except Exception:
        pass

    return output_hint or inspect_hint


def _control_recording_for_trace(trace: str, replay_bin: str | None = None) -> Path | None:
    path = Path(trace)
    if path.suffix != ".retrace":
        return path

    replay = replay_bin or replay_binary_path()
    try:
        completed = subprocess.run(
            [replay, "--recording", str(path), "--extract"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None

    index_path = path.with_suffix(".d") / "index.json"
    try:
        decoded = json.loads(index_path.read_text(encoding="utf-8"))
        pid = int(decoded.get("root", {}).get("pid") or 0)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if pid <= 0:
        return None

    pidfile = index_path.parent / f"{pid}.bin"
    return pidfile if pidfile.exists() else None


def _recording_cwd_for_trace(trace: str) -> Path | None:
    path = Path(trace)
    index_path = path.with_suffix(".d") / "index.json" if path.suffix == ".retrace" else path.parent / "index.json"
    try:
        decoded = json.loads(index_path.read_text(encoding="utf-8"))
        cwd = decoded.get("root", {}).get("preamble", {}).get("cwd")
    except (OSError, TypeError, json.JSONDecodeError):
        return None
    return Path(cwd).resolve() if isinstance(cwd, str) and cwd else None


def _select_pytest_failure_candidate(report: dict[str, Any]) -> dict[str, Any] | None:
    candidates = report.get("ranked_candidates")
    if not isinstance(candidates, list):
        return None

    def usable(candidate: Any) -> bool:
        if not isinstance(candidate, dict):
            return False
        location = candidate.get("location")
        if not isinstance(location, dict):
            return False
        filename = location.get("filename") or location.get("file")
        return bool(filename) and int(location.get("line") or 0) > 0

    usable_candidates = [candidate for candidate in candidates if usable(candidate)]
    if not usable_candidates:
        return None

    def priority(candidate: dict[str, Any]) -> tuple[int, int]:
        exception = candidate.get("exception") if isinstance(candidate.get("exception"), dict) else {}
        classification = str(candidate.get("classification") or "")
        exc_type = str(exception.get("type") or "")
        score = int(candidate.get("score") or 0)
        preferred = 0
        if classification == "application":
            preferred += 10
        if exc_type == "AssertionError":
            preferred += 5
        return (preferred, score)

    selected = max(usable_candidates, key=priority)
    location = selected["location"]
    exception = selected.get("exception") if isinstance(selected.get("exception"), dict) else {}
    filename = location.get("filename") or location.get("file") or ""
    return {
        "filename": str(filename),
        "line": int(location.get("line") or 0),
        "function": str(location.get("function") or ""),
        "exception_type": str(exception.get("type") or ""),
        "exception_message": str(exception.get("message") or ""),
        "classification": str(selected.get("classification") or ""),
        "rank": selected.get("rank"),
        "score": selected.get("score"),
    }


_PYTEST_FAILURE_LOCATION_RE = re.compile(
    r"^(?P<file>.+?\.py):(?P<line>[0-9]+)(?::\s+in\s+(?P<function>\S+)|:\s+(?P<exception_type>[A-Za-z_][A-Za-z0-9_.]*))\s*$"
)
_PYTEST_FAILED_NODE_RE = re.compile(r"^FAILED\s+(?P<file>.+?\.py)::(?P<function>\S+)\s*$")
_PYTEST_EXCEPTION_RE = re.compile(r"^E\s+(?P<type>[A-Za-z_][A-Za-z0-9_.]*)(?::\s*(?P<message>.*))?$")


def _pytest_failure_hint_from_replay_output(
    recording: Path,
    *,
    cwd: Path | None,
    timeout: int,
    replay_bin: str | None = None,
) -> dict[str, Any] | None:
    try:
        completed = subprocess.run(
            [replay_bin or replay_binary_path(), str(recording)],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return _pytest_failure_hint_from_output(
        (completed.stdout or "") + (completed.stderr or ""),
        cwd=cwd,
    )


def _pytest_failure_hint_from_output(text: str, *, cwd: Path | None = None) -> dict[str, Any] | None:
    lines = text.splitlines()
    candidates: list[dict[str, Any]] = []
    for idx, line in enumerate(lines):
        match = _PYTEST_FAILURE_LOCATION_RE.match(line.strip())
        if not match:
            continue
        filename = match.group("file")
        path = Path(filename)
        if not path.is_absolute() and cwd is not None:
            path = cwd / path
        function = match.group("function") or _pytest_failed_function_near(lines, filename)
        exception_type, exception_message = _pytest_exception_near(lines, idx)
        if match.group("exception_type") and not exception_type:
            exception_type = match.group("exception_type")
        candidates.append(
            {
                "filename": str(path.resolve()),
                "line": int(match.group("line")),
                "function": function or "<module>",
                "exception_type": exception_type,
                "exception_message": exception_message,
                "classification": "application",
                "rank": None,
                "score": 0,
            }
        )
    if not candidates:
        return None
    for hint in candidates:
        if hint["function"].startswith("test_"):
            return hint
    for hint in candidates:
        if hint["function"] != "<module>":
            return hint
    return candidates[0]


def _pytest_failed_function_near(lines: list[str], filename: str) -> str:
    stem = Path(filename).name
    for line in lines:
        match = _PYTEST_FAILED_NODE_RE.match(line.strip())
        if match and match.group("file").endswith(stem):
            return match.group("function")
    return ""


def _pytest_exception_near(lines: list[str], start: int) -> tuple[str, str]:
    for line in lines[start + 1:start + 8]:
        match = _PYTEST_EXCEPTION_RE.match(line.strip())
        if match:
            return match.group("type"), match.group("message") or ""
    return "AssertionError", ""


def _exception_and_frames_from_pytest_hint(
    hint: dict[str, Any],
    text: str,
) -> tuple[dict[str, str] | None, list[dict[str, Any]]]:
    exception = None
    if hint.get("exception_type"):
        exception = {
            "type": str(hint["exception_type"]),
            "message": str(hint.get("exception_message") or ""),
        }
    path = str(hint.get("filename") or "")
    function = str(hint.get("function") or "<module>")
    line = int(hint.get("line") or 0)
    frames = [
        {
            "id": 0,
            "name": function,
            "source": {"name": Path(path).name or path, "path": path},
            "line": line,
            "column": 0,
        }
    ]
    parsed_exception, parsed_frames = _parse_python_traceback(text)
    if parsed_frames:
        application_frames = _application_dap_frames(parsed_frames)
        if application_frames:
            return parsed_exception or exception, application_frames
    return exception, frames


def _prime_pytest_failure_breakpoint(executor: DAPExecutor, hint: dict[str, Any]) -> list[dict[str, Any]]:
    path = str(hint.get("filename") or "")
    line = int(hint.get("line") or 0)
    if not path or line <= 0:
        return []

    steps: list[tuple[str, dict[str, Any]]] = [
        ("start_replay_session", {}),
        ("set_exception_breakpoints", {"filters": []}),
        (
            "set_breakpoints",
            {
                "source": {"path": path},
                "breakpoints": [{"line": line}],
            },
        ),
        ("continue_execution", {"thread_id": 1}),
        ("get_stack_trace", {"thread_id": 1, "levels": 20}),
    ]
    transcript: list[dict[str, Any]] = []
    for tool, arguments in steps:
        result = _json_clone(executor.execute(tool, arguments))
        transcript.append({"tool": tool, "arguments": arguments, "result": result})
        if not result.get("ok"):
            break
    return transcript


def _executor_session_state(executor: DAPExecutor | StubExecutor) -> dict[str, Any]:
    session = getattr(executor, "session", None)
    state = getattr(session, "state", None)
    return _json_clone(state) if isinstance(state, dict) else {"state": "not_started"}


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.task and args.task_args:
        args.task = " ".join(args.task_args)
    if not args.task:
        parser.error("provide --task or a positional debugging request")
    args.time_budget_ms = _parse_duration_ms(args.time_budget)

    try:
        artifact = run_driver(args)
        _write_reports(args, artifact)
    except Exception as exc:
        _write_reports(args, _error_artifact(args, exc))
        print(f"retrace-ai-driver: {exc}", file=sys.stderr)
        return 1

    json.dump(artifact, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrace-ai-driver")
    parser.add_argument("--server", default=os.environ.get("RETRACE_AI_SERVER", DEFAULT_AI_SERVER))
    parser.add_argument("--tool-executor", default=os.environ.get("RETRACE_TOOL_EXECUTOR", "dap"))
    parser.add_argument("--trace", default="")
    parser.add_argument("--target", default=os.environ.get("RETRACE_AI_TARGET", "target_application"))
    parser.add_argument("--replay-bin", default=os.environ.get("RETRACE_REPLAY_BIN", ""))
    parser.add_argument("--report-out", default="")
    parser.add_argument("--report-md", default="")
    parser.add_argument("--task", default=os.environ.get("RETRACE_AI_TASK", ""))
    parser.add_argument("--max-tool-calls", type=int, default=int(os.environ.get("RETRACE_AI_MAX_TOOL_CALLS", "20") or "20"))
    parser.add_argument("--time-budget", default=os.environ.get("RETRACE_AI_TIME_BUDGET", ""))
    parser.add_argument("--max-output-tokens", default=os.environ.get("RETRACE_AI_MAX_OUTPUT_TOKENS", ""))
    parser.add_argument("task_args", nargs="*")
    return parser


def _build_executor(args: argparse.Namespace) -> DAPExecutor | StubExecutor:
    if args.tool_executor in {"", "dap", "local-recording"}:
        return DAPExecutor(args.trace, args.replay_bin or None)
    if args.tool_executor == "stub":
        return StubExecutor()
    raise DriverError(f"unknown tool executor {args.tool_executor!r}")


def _write_reports(args: argparse.Namespace, artifact: dict[str, Any]) -> None:
    if args.report_out:
        path = Path(args.report_out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.report_md:
        path = Path(args.report_md)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_render_markdown(artifact), encoding="utf-8")


def _json_clone(value: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(value))


def _artifact(
    args: argparse.Namespace,
    debug_session_id: str,
    report: Any,
    transcript: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(report, dict):
        report = {
            "status": "error",
            "failure_domain": "driver",
            "failure_category": "invalid_service_response",
            "title": "Invalid Retrace AI service report",
            "summary": "The Retrace AI service returned a final_report without a report object.",
            "evidence": [],
            "limitations": [],
        }
    return {
        "kind": "retrace_ai_driver_run",
        "status": report.get("status", "complete"),
        "debug_session_id": debug_session_id,
        "trace": str(Path(args.trace).resolve()) if args.trace else "",
        "target": args.target,
        "report": report,
        "transcript": transcript,
    }


def _error_artifact(args: argparse.Namespace, exc: Exception) -> dict[str, Any]:
    return {
        "kind": "retrace_ai_driver_run",
        "status": "error",
        "debug_session_id": "",
        "trace": str(Path(args.trace).resolve()) if args.trace else "",
        "target": args.target,
        "report": {
            "status": "blocked",
            "failure_domain": "driver",
            "failure_category": "driver_or_service_error",
            "title": "Retrace AI debugger did not complete",
            "summary": str(exc),
            "root_cause": {
                "claim": "The local driver or hosted Retrace AI service failed before returning a final report.",
                "confidence": "high",
                "why": str(exc),
            },
            "evidence": [],
            "limitations": [
                "The target recording was created, but automated diagnosis did not complete.",
            ],
        },
        "transcript": [],
    }


def _render_markdown(artifact: dict[str, Any]) -> str:
    report = artifact.get("report") if isinstance(artifact.get("report"), dict) else {}
    title = str(report.get("title") or "Retrace AI Driver Report")
    lines = [f"# {title}", ""]
    summary = report.get("summary")
    if isinstance(summary, str) and summary:
        lines.extend([summary, ""])
    root = report.get("root_cause")
    if isinstance(root, dict) and isinstance(root.get("claim"), str):
        lines.extend(["## Root Cause", "", root["claim"], ""])
    evidence = report.get("evidence")
    if isinstance(evidence, list) and evidence:
        lines.extend(["## Evidence", ""])
        for item in evidence:
            if isinstance(item, dict):
                lines.append(f"- {item.get('summary') or item.get('source') or item}")
            else:
                lines.append(f"- {item}")
        lines.append("")
    if artifact.get("transcript"):
        lines.extend(["## Tool Transcript", ""])
        for idx, action in enumerate(artifact["transcript"], 1):
            result = action.get("result") if isinstance(action.get("result"), dict) else {}
            lines.append(f"{idx}. `{action.get('tool')}` - {result.get('summary', 'completed')}")
        lines.append("")
    return "\n".join(lines)


def _read_dap_message(stream) -> dict[str, Any] | None:
    content_length = -1
    while True:
        line = stream.readline()
        if not line:
            return None
        text = line.decode("ascii", "replace").strip()
        if text == "":
            break
        if text.lower().startswith("content-length:"):
            content_length = int(text.split(":", 1)[1].strip())
    if content_length < 0:
        return None
    body = stream.read(content_length)
    return json.loads(body.decode("utf-8"))


def _parse_dap_error_response(
    resp: dict[str, Any],
    output: str,
    stderr_text: str,
) -> DAPRequestError:
    command = str(resp.get("command") or "")
    message = str(resp.get("message") or f"DAP {command or '<unknown>'} request failed")
    category = "dap_protocol"
    code = "dap_request_failed"
    control_method: str | None = None
    body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
    retrace = body.get("retrace") if isinstance(body.get("retrace"), dict) else {}
    if isinstance(retrace.get("category"), str) and retrace["category"]:
        category = retrace["category"]
    if isinstance(retrace.get("code"), str) and retrace["code"]:
        code = retrace["code"]
    if isinstance(retrace.get("control_method"), str) and retrace["control_method"]:
        control_method = retrace["control_method"]
    details = [message]
    if output.strip():
        details.append("dap output: " + _tail(output.strip(), 3000))
    if stderr_text.strip():
        details.append("stderr: " + _tail(stderr_text.strip(), 1000))
    return DAPRequestError(
        "; ".join(details),
        command=command,
        category=category,
        code=code,
        control_method=control_method,
    )


def _dap_frame_path(frame: dict[str, Any]) -> str:
    source = frame.get("source") if isinstance(frame.get("source"), dict) else {}
    return str(source.get("path") or "")


def _application_dap_frames(frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        path = _dap_frame_path(frame)
        if path and _is_internal_path(path):
            continue
        selected.append(frame)
    return selected


def _stack_frames(body: dict[str, Any]) -> list[dict[str, Any]]:
    frames = []
    raw_frames = body.get("stackFrames") if isinstance(body.get("stackFrames"), list) else []
    for idx, frame in enumerate(raw_frames):
        if not isinstance(frame, dict):
            continue
        source = frame.get("source") if isinstance(frame.get("source"), dict) else {}
        path = str(source.get("path") or "")
        frames.append(
            {
                "id": int(frame.get("id") if isinstance(frame.get("id"), int) else idx),
                "name": str(frame.get("name") or "<unknown>"),
                "source": {"name": str(source.get("name") or Path(path).name or "<unknown>"), "path": path},
                "line": int(frame.get("line") or 0),
                "column": int(frame.get("column") or 0),
            }
        )
    return frames


_TRACEBACK_FRAME_RE = re.compile(r'^\s*File "([^"]+)", line ([0-9]+), in ([^\s]+)\s*$')
_EXCEPTION_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_.]*):\s*(.*)$")


def _parse_python_traceback(text: str) -> tuple[dict[str, str] | None, list[dict[str, Any]]]:
    frames: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None
    exception: dict[str, str] | None = None
    in_traceback = False

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("Traceback (most recent call last):"):
            in_traceback = True
            continue
        if in_traceback and stripped.startswith("------"):
            break
        match = _TRACEBACK_FRAME_RE.match(line)
        if match:
            if pending is not None:
                frames.append(pending)
            path = match.group(1)
            pending = {
                "id": len(frames),
                "name": match.group(3),
                "source": {"name": Path(path).name or path, "path": path},
                "line": int(match.group(2)),
                "column": 0,
            }
            continue
        if pending is not None and stripped:
            match = _EXCEPTION_LINE_RE.match(stripped) if in_traceback and exception is None else None
            if match:
                frames.append(pending)
                pending = None
                exception = {"type": match.group(1), "message": match.group(2)}
                continue
            pending["code_text"] = stripped
            frames.append(pending)
            pending = None
            continue
        if in_traceback and exception is None and not stripped.startswith("replay:"):
            match = _EXCEPTION_LINE_RE.match(stripped)
            if match:
                exception = {"type": match.group(1), "message": match.group(2)}

    if pending is not None:
        frames.append(pending)

    if exception is None:
        for line in reversed(text.strip().splitlines()):
            stripped = line.strip()
            if stripped.startswith("replay:"):
                continue
            match = _EXCEPTION_LINE_RE.match(stripped)
            if match:
                exception = {"type": match.group(1), "message": match.group(2)}
                break

    frames.reverse()
    for idx, frame in enumerate(frames):
        frame["id"] = idx
    return exception, frames


def _application_stack_summary(frames: list[dict[str, Any]]) -> str:
    if not frames:
        return "No application stack frames are available for the current failure."
    top = frames[0]
    top_source = top.get("source") if isinstance(top.get("source"), dict) else {}
    path = str(top_source.get("path") or top_source.get("name") or "<unknown>")
    return (
        f"Application stack has {len(frames)} frame(s); "
        f"top frame is {Path(path).name}:{top.get('line', 0)} "
        f"in {top.get('name', '<unknown>')}."
    )


def _stack_summary(frames: list[dict[str, Any]]) -> str:
    return _application_stack_summary(frames)


def _exception_summary(data: dict[str, Any]) -> str:
    exception_id = str(data.get("exceptionId") or "")
    description = str(data.get("description") or "")
    if exception_id and exception_id != "<none>":
        if description:
            return f"DAP exceptionInfo reported {exception_id}: {description}."
        return f"DAP exceptionInfo reported {exception_id}."
    return "DAP exceptionInfo returned no exception for the current stop."


def _source_from_arguments(arguments: dict[str, Any], frames: list[dict[str, Any]]) -> tuple[str, int]:
    source = arguments.get("source") if isinstance(arguments.get("source"), dict) else {}
    path = source.get("path") if isinstance(source.get("path"), str) else ""
    line = source.get("line") if isinstance(source.get("line"), int) else 0
    frame_id = arguments.get("frame_id")
    if isinstance(frame_id, int) and frames:
        frame = frames[min(max(frame_id, 0), len(frames) - 1)]
        frame_source = frame.get("source") if isinstance(frame.get("source"), dict) else {}
        path = path or str(frame_source.get("path") or "")
        line = line or int(frame.get("line") or 0)
    elif (not path or not line) and frames:
        frame = frames[0]
        frame_source = frame.get("source") if isinstance(frame.get("source"), dict) else {}
        path = path or str(frame_source.get("path") or "")
        line = line or int(frame.get("line") or 0)
    return path, line


def _source_window(content: str, line: int, before: int, after: int) -> list[dict[str, Any]]:
    lines = content.splitlines()
    if not lines:
        return []
    start = max(1, line - before)
    end = min(len(lines), line + after)
    return [
        {"line": idx, "text": _bounded_source_line(lines[idx - 1]), "current": idx == line}
        for idx in range(start, end + 1)
        if lines[idx - 1].strip()
    ]


def _bounded_source_line(text: str) -> str:
    if len(text) <= MAX_SOURCE_CONTEXT_LINE_CHARS:
        return text
    suffix = " ... <truncated>"
    return text[: MAX_SOURCE_CONTEXT_LINE_CHARS - len(suffix)] + suffix


def _tool_error(tool: str, code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "summary": f"{tool} failed.",
        "error": {
            "domain": "driver",
            "category": "tool_contract",
            "code": code,
            "message": message,
        },
    }


def _application_context_unavailable(
    session: DAPSession,
    recovery_note: str | None,
) -> dict[str, Any]:
    message = recovery_note or (
        "No application stack frames were recovered from the current stop or recorded failure output."
    )
    return {
        "ok": False,
        "summary": "Could not recover application stack frames from the recording.",
        "error": {
            "domain": "application",
            "category": "context_unavailable",
            "code": "no_application_frames",
            "message": message,
        },
        "session": session.state,
    }


def _application_inspection_unavailable(
    tool: str,
    session_state: dict[str, Any] | None,
    guidance: str,
) -> dict[str, Any]:
    result = {
        "ok": False,
        "summary": (
            f"{tool} needs an inspectable application stop; "
            "reposition at application source and continue before inspecting."
        ),
        "error": {
            "domain": "application",
            "category": "wrong_stop_location",
            "code": "inspection_unavailable",
            "message": guidance,
        },
    }
    if session_state is not None:
        result["session"] = session_state
    return result


def _dap_error(tool: str, exc: Exception, session: dict[str, Any] | DAPSession | None) -> dict[str, Any]:
    session_state: dict[str, Any] | None
    if isinstance(session, DAPSession):
        session_state = session.state
    elif isinstance(session, dict):
        session_state = session
    else:
        session_state = None

    if isinstance(exc, DAPRequestError) and exc.category == "inspection_unavailable":
        return _application_inspection_unavailable(
            tool,
            session_state,
            "Set a source breakpoint on application code, continue, then retry this tool.",
        )

    result = {
        "ok": False,
        "summary": f"{tool} failed through the DAP proxy.",
        "error": {
            "domain": "driver",
            "category": "dap_protocol",
            "code": "dap_request_failed",
            "message": str(exc),
        },
    }
    if isinstance(exc, DAPRequestError):
        result["error"]["code"] = exc.code
        if exc.category != "dap_protocol":
            result["error"]["category"] = exc.category
        if exc.control_method:
            result["error"]["control_method"] = exc.control_method
    if session_state is not None:
        result["session"] = session_state
    return result


def _parse_duration_ms(value: str) -> int | None:
    value = value.strip()
    if not value:
        return None
    try:
        if value.endswith("ms"):
            return int(float(value[:-2]))
        if value.endswith("s"):
            return int(float(value[:-1]) * 1000)
        if value.endswith("m"):
            return int(float(value[:-1]) * 60_000)
        return int(float(value) * 1000)
    except ValueError as exc:
        raise DriverError(f"invalid --time-budget {value!r}") from exc


def _package_version() -> str:
    try:
        from importlib.metadata import version

        return version("retracesoftware")
    except Exception:
        return "0+unknown"


def _install_id() -> str:
    env_value = os.environ.get("RETRACE_INSTALL_ID", "").strip()
    if env_value:
        return env_value
    path = Path(os.environ.get("RETRACE_INSTALL_ID_FILE", "") or Path.home() / ".retracesoftware" / "install_id")
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        path.parent.mkdir(parents=True, exist_ok=True)
        value = str(uuid.uuid4())
        path.write_text(value + "\n", encoding="utf-8")
        return value
    except OSError:
        return str(uuid.uuid4())


def _tail(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[-limit:]


if __name__ == "__main__":
    raise SystemExit(main())
