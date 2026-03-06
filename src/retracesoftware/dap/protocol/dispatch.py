"""DAP request dispatcher.

Routes incoming DAP messages to handler methods by command name and provides
helpers for sending responses and events.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from . import framing, types

log = logging.getLogger(__name__)


class Dispatcher:
    """Read DAP messages from *rfile*, dispatch to handlers, write to *wfile*.

    Subclass and define ``handle_<command>`` methods (e.g. ``handle_initialize``,
    ``handle_launch``).  Each handler receives the full request dict and returns
    a body dict (or None).  To send async events, call :meth:`send_event`.
    """

    def __init__(self, rfile, wfile) -> None:
        self._rfile = rfile
        self._wfile = wfile
        self._running = False
        self._write_lock = threading.Lock()

    # -- sending ------------------------------------------------------------

    def send(self, msg: dict[str, Any]) -> None:
        with self._write_lock:
            framing.write_message(self._wfile, msg)
        log.debug(">>> %s", _summary(msg))

    def send_event(self, name: str, body: dict[str, Any] | None = None, **kwargs) -> None:
        self.send(types.event(name, body, **kwargs))

    def send_response(self, request: dict, body: dict[str, Any] | None = None, **kwargs) -> None:
        self.send(types.response(request, body, **kwargs))

    def send_error(self, request: dict, message: str) -> None:
        self.send(types.error_response(request, message))

    # -- main loop ----------------------------------------------------------

    def handle_one(self) -> bool:
        """Read and dispatch one message.  Returns False on EOF."""
        msg = framing.read_message(self._rfile)
        if msg is None:
            return False
        log.debug("<<< %s", _summary(msg))
        self._dispatch(msg)
        return True

    def run(self) -> None:
        """Read and dispatch messages until EOF or disconnect."""
        self._running = True
        while self._running:
            if not self.handle_one():
                break

    def stop(self) -> None:
        self._running = False

    # -- dispatch -----------------------------------------------------------

    def _dispatch(self, msg: dict) -> None:
        msg_type = msg.get("type")
        if msg_type == "request":
            self._handle_request(msg)
        elif msg_type == "response":
            pass  # we don't send requests to the client (yet)
        elif msg_type == "event":
            pass  # client-originated events are rare

    def _handle_request(self, request: dict) -> None:
        command = request.get("command", "")
        handler: Callable | None = getattr(self, f"handle_{command}", None)

        if handler is None:
            self.send_error(request, f"Unsupported command: {command}")
            return

        try:
            result = handler(request)
            if isinstance(result, dict):
                self.send_response(request, result)
            elif result is None:
                self.send_response(request)
            # If handler returns a sentinel (e.g. False), it sent its own response.
        except Exception as exc:
            log.exception("Handler error for %s", command)
            self.send_error(request, str(exc))


def _summary(msg: dict) -> str:
    """One-line summary of a DAP message for logging."""
    t = msg.get("type", "?")
    if t == "request":
        return f"request #{msg.get('seq')} {msg.get('command')}"
    if t == "response":
        cmd = msg.get("command", "?")
        ok = "ok" if msg.get("success") else "FAIL"
        return f"response #{msg.get('request_seq')} {cmd} {ok}"
    if t == "event":
        return f"event {msg.get('event')}"
    return f"unknown {t}"
