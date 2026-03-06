"""Retrace DAP debug adapter.

Extends :class:`Dispatcher` with handlers for every DAP command that
retracesoftware supports.  The adapter owns its Unix-domain socket
connection and can :meth:`reconnect` after a fork.  All message handling
is driven inline on the main thread: :meth:`run_until_configured` handles
the initial handshake, and :class:`DebugHooks` callbacks process messages
while the target is paused at a breakpoint or step.
"""

from __future__ import annotations

import logging
import os
import socket as _sock
from typing import Any

from .protocol.dispatch import Dispatcher
from .protocol import types
from .debug.cursor import Cursor
from .debug.breakpoints import BreakpointManager
from .debug.stepping import StepManager
from .debug.inspection import Inspector
from .debug.hooks import DebugHooks, THREAD_ID

log = logging.getLogger(__name__)


class Adapter(Dispatcher):

    def __init__(self, socket_path: str) -> None:
        self._socket_path = socket_path
        self._socket: _sock.socket | None = None
        rfile, wfile = self._connect()
        super().__init__(rfile, wfile)
        self.cursor = Cursor()
        self.breakpoints = BreakpointManager()
        self.step_manager = StepManager(self.cursor)
        self.inspector = Inspector()
        self.hooks = DebugHooks(self)
        self._configured = False

    def _connect(self):
        """Open a Unix-domain connection and return (rfile, wfile)."""
        sock = _sock.socket(_sock.AF_UNIX, _sock.SOCK_STREAM)
        sock.connect(self._socket_path)
        self._socket = sock
        return sock.makefile("rb"), sock.makefile("wb")

    def reconnect(self) -> None:
        """Close the inherited connection and open a fresh one.

        Intended for use in a child process after fork so the child gets
        its own connection to the Go replay process.
        """
        self._close_transport()
        self._rfile, self._wfile = self._connect()

    def close(self) -> None:
        """Shut down the adapter and close the underlying socket."""
        self.stop()
        self._close_transport()

    def _close_transport(self) -> None:
        for f in (self._rfile, self._wfile):
            try:
                f.close()
            except Exception:
                pass
        if self._socket is not None:
            try:
                self._socket.close()
            except Exception:
                pass
            self._socket = None

    # -- handshake ----------------------------------------------------------

    def run_until_configured(self) -> None:
        """Block, reading DAP messages, until configurationDone is received."""
        while not self._configured:
            if not self.handle_one():
                break

    # -- lifecycle ----------------------------------------------------------

    def handle_initialize(self, request: dict) -> dict:
        self.send_event("initialized")
        return types.CAPABILITIES

    def handle_launch(self, request: dict) -> dict:
        return {}

    def handle_configurationDone(self, request: dict) -> dict:
        self._configured = True
        return {}

    def handle_disconnect(self, request: dict) -> dict:
        if self.hooks.is_paused:
            self.hooks.set_step_mode(None)
            self.hooks.resume()
        self.stop()
        return {}

    def handle_terminate(self, request: dict) -> dict:
        self.send(types.terminated_event())
        self.stop()
        return {}

    def handle_restart(self, request: dict) -> dict:
        self.cursor.reset()
        self.send(types.stopped_event("entry", thread_id=0))
        return {}

    # -- threads ------------------------------------------------------------

    def handle_threads(self, request: dict) -> dict:
        return {"threads": [{"id": THREAD_ID, "name": "MainThread"}]}

    # -- breakpoints --------------------------------------------------------

    def handle_setBreakpoints(self, request: dict) -> dict:
        args = request.get("arguments", {})
        source = args.get("source", {})
        path = source.get("path", "")
        bp_specs = args.get("breakpoints", [])
        result = self.breakpoints.set_breakpoints(path, bp_specs)
        return {"breakpoints": result}

    def handle_setFunctionBreakpoints(self, request: dict) -> dict:
        args = request.get("arguments", {})
        bp_specs = args.get("breakpoints", [])
        result = self.breakpoints.set_function_breakpoints(bp_specs)
        return {"breakpoints": result}

    def handle_setExceptionBreakpoints(self, request: dict) -> dict:
        args = request.get("arguments", {})
        filters = args.get("filters", [])
        self.breakpoints.set_exception_filters(filters)
        return {}

    # -- stepping -----------------------------------------------------------

    def handle_continue(self, request: dict) -> dict:
        if self.hooks.is_paused:
            self.hooks.set_step_mode(None)
            self.hooks.resume()
        return {"allThreadsContinued": True}

    def handle_next(self, request: dict) -> dict:
        if self.hooks.is_paused:
            depth = _paused_depth(self.hooks)
            self.hooks.set_step_mode("next", depth)
            self.hooks.resume()
        return {}

    def handle_stepIn(self, request: dict) -> dict:
        if self.hooks.is_paused:
            self.hooks.set_step_mode("step_in")
            self.hooks.resume()
        return {}

    def handle_stepOut(self, request: dict) -> dict:
        if self.hooks.is_paused:
            depth = _paused_depth(self.hooks)
            self.hooks.set_step_mode("step_out", depth)
            self.hooks.resume()
        return {}

    def handle_pause(self, request: dict) -> dict:
        if not self.hooks.is_paused:
            self.hooks.request_pause()
        return {}

    # -- inspection ---------------------------------------------------------

    def handle_stackTrace(self, request: dict) -> dict:
        args = request.get("arguments", {})
        thread_id = args.get("threadId", 0)
        start_frame = args.get("startFrame", 0)
        levels = args.get("levels", 0)

        if self.hooks.is_paused and self.hooks.paused_frame:
            return self.inspector.stack_trace_from_frame(
                self.hooks.paused_frame, thread_id, start_frame, levels,
            )
        return self.inspector.stack_trace(thread_id, start_frame, levels)

    def handle_scopes(self, request: dict) -> dict:
        args = request.get("arguments", {})
        frame_id = args.get("frameId", 0)
        return self.inspector.scopes(frame_id)

    def handle_variables(self, request: dict) -> dict:
        args = request.get("arguments", {})
        ref = args.get("variablesReference", 0)
        start = args.get("start", 0)
        count = args.get("count", 0)
        return self.inspector.variables(ref, start, count)

    def handle_evaluate(self, request: dict) -> dict:
        args = request.get("arguments", {})
        expression = args.get("expression", "")
        frame_id = args.get("frameId")
        return self.inspector.evaluate(expression, frame_id)

    def handle_exceptionInfo(self, request: dict) -> dict:
        args = request.get("arguments", {})
        thread_id = args.get("threadId", 0)
        return self.inspector.exception_info(thread_id)

    # -- source -------------------------------------------------------------

    def handle_source(self, request: dict) -> dict:
        args = request.get("arguments", {})
        source = args.get("source", {})
        path = source.get("path", "")
        if path and not os.path.isabs(path):
            path = os.path.abspath(path)
        try:
            with open(path) as f:
                content = f.read()
            return {"content": content, "mimeType": "text/x-python"}
        except OSError as exc:
            raise RuntimeError(f"Cannot read source: {exc}") from exc

    # -- goto ---------------------------------------------------------------

    def handle_gotoTargets(self, request: dict) -> dict:
        args = request.get("arguments", {})
        line = args.get("line", 0)
        return {
            "targets": [{
                "id": line,
                "label": f"Line {line}",
                "line": line,
            }]
        }

    def handle_goto(self, request: dict) -> dict:
        args = request.get("arguments", {})
        thread_id = args.get("threadId", 0)
        self.send(types.stopped_event("goto", thread_id=thread_id))
        return {}

    # -- retrace custom extensions ------------------------------------------

    def handle_retrace_getCursor(self, request: dict) -> dict:
        return {"cursor": self.cursor.to_dict()}

    def handle_retrace_runToCursor(self, request: dict) -> dict:
        self.send(types.stopped_event("goto", thread_id=0))
        return {}

    def handle_retrace_fork(self, request: dict) -> dict:
        return {}

    def handle_retrace_listPids(self, request: dict) -> dict:
        return {"pids": []}

    # -- override dispatch to handle slash commands -------------------------

    def _handle_request(self, request: dict) -> None:
        command = request.get("command", "")
        method_name = f"handle_{command.replace('/', '_')}"
        handler = getattr(self, method_name, None)

        if handler is None:
            self.send_error(request, f"Unsupported command: {command}")
            return

        try:
            result = handler(request)
            if isinstance(result, dict):
                self.send_response(request, result)
            elif result is None:
                self.send_response(request)
        except Exception as exc:
            log.exception("Handler error for %s", command)
            self.send_error(request, str(exc))


def _paused_depth(hooks: DebugHooks) -> int:
    """Return the call stack depth of the paused target frame."""
    if not hooks.paused_frame:
        return 0
    from .debug.hooks import _frame_depth
    return _frame_depth(hooks.paused_frame)
