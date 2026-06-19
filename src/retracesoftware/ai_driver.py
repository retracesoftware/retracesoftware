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

from retracesoftware.replay import binary_path as replay_binary_path
from retracesoftware.retracepython import DEFAULT_AI_SERVER


DAP_SESSION_ID = "dap-replay-1"
DEFAULT_TIMEOUT = 30.0
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
        message = resp.get("message") or f"DAP {resp.get('command', '<unknown>')} request failed"
        details = [str(message)]
        if self.output.strip():
            details.append("dap output: " + _tail(self.output.strip(), 3000))
        if self.stderr_text.strip():
            details.append("stderr: " + _tail(self.stderr_text.strip(), 1000))
        raise DriverError("; ".join(details))

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
        self.close()
        session = DAPSession(self.replay_bin, trace)
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
        if session.synthetic_exception is not None and session.frames:
            return {
                "ok": True,
                "summary": _stack_summary(session.frames),
                "data": {
                    "stack_frames": session.frames,
                    "total_frames": len(session.frames),
                    "source": "dap.output.traceback",
                },
                "session": session.state,
            }
        resp = session.request(
            "stackTrace",
            {
                "threadId": int(arguments.get("thread_id") or 1),
                "startFrame": int(arguments.get("start_frame") or 0),
                "levels": int(arguments.get("levels") or 20),
            },
        )
        body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
        frames = _stack_frames(body)
        source = "dap.stackTrace"
        if not frames:
            exception, fallback_frames = self._direct_replay_traceback(session.trace)
            if exception is not None and fallback_frames:
                session.synthetic_exception = exception
                session.state["state"] = "stopped"
                session.state["last_stop"] = {
                    "reason": "exception",
                    "thread_id": int(arguments.get("thread_id") or 1),
                    "description": exception.get("message", ""),
                }
                frames = fallback_frames
                source = "replay.traceback"
        session.frames = frames
        return {
            "ok": True,
            "summary": _stack_summary(frames),
            "data": {
                "stack_frames": frames,
                "total_frames": int(body.get("totalFrames") or len(frames)),
                "source": source,
            },
            "session": session.state,
        }

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
        resp = session.request("variables", {"variablesReference": ref})
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
        return _parse_python_traceback((completed.stdout or "") + (completed.stderr or ""))

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
        start_payload = {
            "task": {
                "goal": "diagnose_failure",
                "investigation_target": args.target,
                "user_request": args.task,
            },
            "trace": str(Path(args.trace).resolve()) if args.trace else "",
            "available_tools": executor.available_tools(),
            "initial_observation": {"summary": "Driver initialized. No replay tool has been called yet."},
            "session": {"state": "not_started"},
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


def _stack_summary(frames: list[dict[str, Any]]) -> str:
    if not frames:
        return "DAP stack trace returned no frames at the current replay stop."
    top = frames[0]
    top_source = top.get("source") if isinstance(top.get("source"), dict) else {}
    path = str(top_source.get("path") or top_source.get("name") or "<unknown>")
    return (
        f"DAP stack trace has {len(frames)} frame(s); "
        f"top frame is {Path(path).name}:{top.get('line', 0)} "
        f"in {top.get('name', '<unknown>')}."
    )


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
        {"line": idx, "text": lines[idx - 1], "current": idx == line}
        for idx in range(start, end + 1)
    ]


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


def _dap_error(tool: str, exc: Exception, session: dict[str, Any] | None) -> dict[str, Any]:
    result = {
        "ok": False,
        "summary": f"{tool} failed through the DAP proxy.",
        "error": {
            "domain": "retrace",
            "category": "dap_protocol",
            "code": "dap_request_failed",
            "message": str(exc),
        },
    }
    if session is not None:
        result["session"] = session
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
