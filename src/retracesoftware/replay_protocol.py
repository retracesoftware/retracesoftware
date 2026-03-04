import json
import os
import socket
import sys
from types import SimpleNamespace


def _send_line(sock, obj):
    data = (json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8")
    sock.sendall(data)

def _read_line(fileobj):
    line = fileobj.readline()
    if not line:
        return None
    return json.loads(line)

class ProtocolServer:
    def __init__(self, recording, sock_path, fork_id=None):
        self.recording = recording
        self.sock_path = sock_path
        self.fork_id = fork_id
        self.backstop = None
        self._running = True
        self.breakpoints = []
        self.last_stop = {
            "reason": "idle",
            "message_index": 0,
            "cursor": [],
            "thread_cursors": {},
        }

    def _resp_ok(self, req_id, result):
        return {"id": req_id, "type": "response", "ok": True, "result": result}

    def _resp_err(self, req_id, code, message, data=None):
        out = {
            "id": req_id,
            "type": "response",
            "ok": False,
            "error": {"code": code, "message": message},
        }
        if data is not None:
            out["error"]["data"] = data
        return out

    def _connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.sock_path)
        if self.fork_id:
            _send_line(sock, {
                "type": "event",
                "event": "fork_hello",
                "payload": {
                    "fork_id": self.fork_id,
                    "pid": os.getpid(),
                    "message_index": 0,
                    "thread_cursors": {},
                },
            })
        return sock

    def _handle_hello(self, req_id, _params):
        return self._resp_ok(req_id, {
            "protocol_version": 1,
            "python_version": sys.version,
            "capabilities": [
                "hello",
                "set_backstop",
                "set_breakpoints",
                "fork",
                "continue",
                "find_breakpoints",
                "run_to_cursor",
                "state",
                "stack",
                "locals",
                "eval",
                "close",
            ],
        })

    def _handle_set_backstop(self, req_id, params):
        value = params.get("message_index")
        if value is None or int(value) < 0:
            return self._resp_err(req_id, "invalid_backstop", "message_index must be >= 0")
        self.backstop = int(value)
        return self._resp_ok(req_id, {"message_index": self.backstop})

    def _handle_fork(self, req_id, params):
        fork_id = params.get("fork_id")
        if not fork_id:
            return self._resp_err(req_id, "invalid_fork", "fork requires non-empty fork_id")
        pid = os.fork()
        if pid == 0:
            # child
            self.fork_id = fork_id
            return None
        # parent
        return self._resp_ok(req_id, {
            "fork_id": fork_id,
            "child_pid": pid,
            "parent_pid": os.getpid(),
            "state": "forked",
        })

    def _handle_set_breakpoints(self, req_id, params):
        bps = params.get("breakpoints") or []
        normalized = []
        for bp in bps:
            path = bp.get("file")
            line = bp.get("line")
            if not path or line is None:
                continue
            normalized.append({
                "file": os.path.realpath(path),
                "line": int(line),
                "condition": bp.get("condition"),
            })
        self.breakpoints = normalized
        return self._resp_ok(req_id, {"count": len(self.breakpoints)})

    def _run_until_stop(self, target_cursor=None):
        from retracesoftware.proxy.system import System
        from retracesoftware import __main__ as retrace_main
        from retracesoftware.search import ReplayStop
        replay_args = SimpleNamespace(
            recording=self.recording,
            read_timeout=1000,
            verbose=False,
            fork_path="",
            retrace_file_patterns=None,
            chunk_ms=None,
            breakpoint=None,
            control_socket=None,
            protocol_breakpoints=self.breakpoints,
            protocol_cursor=target_cursor,
            protocol_backstop=self.backstop,
        )
        try:
            retrace_main.replay(System(), replay_args)
        except ReplayStop as stop:
            payload = dict(stop.payload)
            payload.setdefault("thread_cursors", {})
            self.last_stop = payload
            return payload
        payload = {
            "reason": "eof",
            "message_index": 0,
            "cursor": [],
            "thread_cursors": {},
        }
        self.last_stop = payload
        return payload

    def _handle_continue(self, req_id, _params):
        payload = self._run_until_stop(target_cursor=None)
        return self._resp_ok(req_id, payload)

    def _handle_find_breakpoints(self, sock, req_id, params):
        file = params.get("file")
        line = params.get("line")
        condition = params.get("condition")
        if not file or not line:
            return self._resp_err(req_id, "invalid_breakpoint", "file and line are required")

        bp = f"{file}:{int(line)}"
        if condition:
            bp = f"{bp}:{condition}"

        class _BackstopReached(Exception):
            pass

        class _HitCapture:
            def __init__(self):
                self._buf = ""
                self.count = 0

            def write(self, text):
                self._buf += text
                while "\n" in self._buf:
                    line_text, self._buf = self._buf.split("\n", 1)
                    line_text = line_text.strip()
                    if not line_text:
                        continue
                    hit = json.loads(line_text)
                    self.count += 1
                    payload = {
                        "request_id": req_id,
                        "message_index": self.count,
                        "cursor": hit.get("cursor", []),
                    }
                    _send_line(sock, {"type": "event", "event": "breakpoint_hit", "payload": payload})
                    if self.count >= backstop_count:
                        raise _BackstopReached()
                return len(text)

            def flush(self):
                return None

        backstop_count = self.backstop if self.backstop is not None else 2**63 - 1
        capture = _HitCapture()
        original_stdout = sys.stdout
        try:
            sys.stdout = capture
            from retracesoftware.proxy.system import System
            from retracesoftware import __main__ as retrace_main
            replay_args = SimpleNamespace(
                recording=self.recording,
                read_timeout=1000,
                verbose=False,
                fork_path="",
                retrace_file_patterns=None,
                chunk_ms=None,
                breakpoint=bp,
                control_socket=None,
            )
            try:
                retrace_main.replay(System(), replay_args)
            except _BackstopReached:
                _send_line(sock, {"type": "event", "event": "stream_end", "payload": {"request_id": req_id, "reason": "backstop", "count": capture.count}})
                return self._resp_ok(req_id, {"reason": "backstop", "count": capture.count, "message_index": capture.count})
        except Exception as exc:
            return self._resp_err(req_id, "search_failed", "find_breakpoints failed", {"error": str(exc)})
        finally:
            sys.stdout = original_stdout

        _send_line(sock, {"type": "event", "event": "stream_end", "payload": {"request_id": req_id, "reason": "complete", "count": capture.count}})
        return self._resp_ok(req_id, {"reason": "complete", "count": capture.count, "message_index": capture.count})

    def _handle_run_to_cursor(self, req_id, params):
        cursor = params.get("cursor")
        if not isinstance(cursor, list):
            return self._resp_err(req_id, "invalid_cursor", "cursor must be a list")
        payload = self._run_until_stop(target_cursor=cursor)
        return self._resp_ok(req_id, payload)

    def _handle_state(self, req_id, _params):
        return self._resp_ok(req_id, {
            "message_index": self.last_stop.get("message_index", 0),
            "thread_cursors": self.last_stop.get("thread_cursors", {}),
            "cursor": self.last_stop.get("cursor", []),
            "stop_reason": self.last_stop.get("reason", "idle"),
            "backstop": self.backstop,
        })

    def _handle_stack(self, req_id, _params):
        return self._resp_ok(req_id, {"frames": []})

    def _handle_locals(self, req_id, _params):
        return self._resp_ok(req_id, {"locals": {}})

    def _handle_eval(self, req_id, params):
        expr = params.get("expr")
        if expr is None:
            return self._resp_err(req_id, "invalid_eval", "expr is required")
        return self._resp_ok(req_id, {"result": None, "repr": "None"})

    def _handle_close(self, req_id, _params):
        self._running = False
        return self._resp_ok(req_id, {"closed": True})

    def run(self):
        sock = self._connect()
        fileobj = sock.makefile("r", encoding="utf-8")
        try:
            while self._running:
                msg = _read_line(fileobj)
                if msg is None:
                    break
                if msg.get("type") != "request":
                    continue
                req_id = msg.get("id")
                method = msg.get("method")
                params = msg.get("params") or {}
                if method == "hello":
                    response = self._handle_hello(req_id, params)
                elif method == "set_backstop":
                    response = self._handle_set_backstop(req_id, params)
                elif method == "fork":
                    response = self._handle_fork(req_id, params)
                    if response is None:
                        sock.close()
                        return ProtocolServer(self.recording, self.sock_path, self.fork_id).run()
                elif method == "set_breakpoints":
                    response = self._handle_set_breakpoints(req_id, params)
                elif method == "continue":
                    response = self._handle_continue(req_id, params)
                elif method == "find_breakpoints":
                    response = self._handle_find_breakpoints(sock, req_id, params)
                elif method == "run_to_cursor":
                    response = self._handle_run_to_cursor(req_id, params)
                elif method == "state":
                    response = self._handle_state(req_id, params)
                elif method == "stack":
                    response = self._handle_stack(req_id, params)
                elif method == "locals":
                    response = self._handle_locals(req_id, params)
                elif method == "eval":
                    response = self._handle_eval(req_id, params)
                elif method == "close":
                    response = self._handle_close(req_id, params)
                else:
                    response = self._resp_err(req_id, "unknown_method", f"Unknown method: {method}")
                _send_line(sock, response)
        finally:
            try:
                fileobj.close()
            except Exception:
                pass
            try:
                sock.close()
            except Exception:
                pass


def run_protocol_server(recording, control_socket):
    server = ProtocolServer(recording=recording, sock_path=control_socket, fork_id=None)
    server.run()
