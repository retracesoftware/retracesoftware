"""Whole-program search modes for replay.

Each search function installs sys.monitoring hooks before the replay runs
and emits JSON lines to the provided output stream for each hit.
"""
import os
import sys
import json
import time
import atexit
import retracesoftware.utils as utils

DISABLE = sys.monitoring.DISABLE


class ReplayStop(Exception):
    def __init__(self, payload):
        super().__init__(payload.get("reason", "stop"))
        self.payload = payload


def install_breakpoint_search(target_file, target_line, condition=None):
    """Install hooks that emit a cursor JSON line for each breakpoint hit.

    Redirects sys.stdout to /dev/null so the replayed program's output
    doesn't mix with search results.  Returns the output stream that
    receives JSON lines (the original stdout).
    """
    search_output = sys.stdout
    sys.stdout = open(os.devnull, 'w')

    utils.install_call_counter()

    tool_id = None
    for tid in range(6):
        try:
            sys.monitoring.use_tool_id(tid, "retrace_search")
            tool_id = tid
            break
        except ValueError:
            continue
    else:
        raise RuntimeError("No free sys.monitoring tool IDs available")

    target_file = os.path.realpath(target_file)
    E = sys.monitoring.events

    if condition is not None:
        code_obj = compile(condition, '<breakpoint-condition>', 'eval')

    def on_py_start(code, instruction_offset):
        if code.co_filename == target_file:
            sys.monitoring.set_local_events(tool_id, code, E.LINE)
        return DISABLE

    def on_line(code, line):
        if line != target_line:
            return DISABLE
        if condition is not None:
            frame = sys._getframe(1)
            try:
                if not eval(code_obj, frame.f_globals, frame.f_locals):
                    return None
            except Exception:
                return None
        cursor = utils.cursor_snapshot().to_dict()
        json.dump({"cursor": cursor}, search_output)
        search_output.write("\n")
        search_output.flush()

    _disable = utils.call_counter_disable_for
    sys.monitoring.register_callback(tool_id, E.PY_START, _disable(on_py_start))
    sys.monitoring.register_callback(tool_id, E.LINE, _disable(on_line))
    sys.monitoring.set_events(tool_id, E.PY_START)

    return search_output


def install_protocol_stop_search(
    breakpoints,
    target_cursor,
    backstop_message_index,
    get_offset,
):
    """Install stop hooks for protocol `continue` and `run_to_cursor`.

    Stop reasons:
    - breakpoint
    - cursor
    - backstop
    """
    utils.install_call_counter()

    tool_id = None
    for tid in range(6):
        try:
            sys.monitoring.use_tool_id(tid, "retrace_protocol_stop")
            tool_id = tid
            break
        except ValueError:
            continue
    else:
        raise RuntimeError("No free sys.monitoring tool IDs available")

    E = sys.monitoring.events
    normalized = []
    for bp in (breakpoints or []):
        file_path = os.path.realpath(bp.get("file", ""))
        line = int(bp.get("line", 0))
        cond_text = bp.get("condition")
        cond_code = compile(cond_text, "<protocol-breakpoint-condition>", "eval") if cond_text else None
        if file_path and line > 0:
            normalized.append((file_path, line, cond_code))

    files_with_breakpoints = {bp[0] for bp in normalized}
    target_cursor_tuple = tuple(target_cursor) if target_cursor else None

    def _raise_stop(reason):
        payload = {
            "reason": reason,
            "message_index": int(get_offset()),
            "cursor": utils.cursor_snapshot().to_dict(),
        }
        raise ReplayStop(payload)

    def _check_common():
        if backstop_message_index is not None:
            if int(get_offset()) >= int(backstop_message_index):
                _raise_stop("backstop")
        if target_cursor_tuple is not None:
            current = tuple(utils.current_call_counts())
            if current == target_cursor_tuple:
                _raise_stop("cursor")

    def on_py_start(code, instruction_offset):
        _check_common()
        if code.co_filename in files_with_breakpoints:
            sys.monitoring.set_local_events(tool_id, code, E.LINE)
        return DISABLE

    def on_py_event(code, instruction_offset):
        _check_common()
        return None

    def on_line(code, line):
        _check_common()
        if not normalized:
            return None
        for bp_file, bp_line, cond_code in normalized:
            if code.co_filename != bp_file or line != bp_line:
                continue
            if cond_code is not None:
                frame = sys._getframe(1)
                try:
                    if not eval(cond_code, frame.f_globals, frame.f_locals):
                        continue
                except Exception:
                    continue
            _raise_stop("breakpoint")
        return None

    _disable = utils.call_counter_disable_for
    sys.monitoring.register_callback(tool_id, E.PY_START, _disable(on_py_start))
    sys.monitoring.register_callback(tool_id, E.PY_RETURN, _disable(on_py_event))
    sys.monitoring.register_callback(tool_id, E.PY_UNWIND, _disable(on_py_event))
    sys.monitoring.register_callback(tool_id, E.LINE, _disable(on_line))
    sys.monitoring.set_events(tool_id, E.PY_START | E.PY_RETURN | E.PY_UNWIND)

    return tool_id


def install_timeslice_search(chunk_ms, get_offset):
    """Install hooks that emit JSON message-offset boundaries by CPU time.

    Emits one JSON line roughly every ``chunk_ms`` milliseconds of process
    compute time while replay executes. The final line is emitted at process
    exit and marks the remainder boundary.
    """
    if chunk_ms <= 0:
        raise ValueError("chunk_ms must be > 0")
    if get_offset is None:
        raise ValueError("get_offset callback is required")

    search_output = sys.stdout
    sys.stdout = open(os.devnull, "w")

    tool_id = None
    for tid in range(6):
        try:
            sys.monitoring.use_tool_id(tid, "retrace_timeslice")
            tool_id = tid
            break
        except ValueError:
            continue
    else:
        raise RuntimeError("No free sys.monitoring tool IDs available")

    E = sys.monitoring.events
    chunk_ns = int(chunk_ms * 1_000_000)
    started_ns = time.process_time_ns()
    next_emit_ns = started_ns + chunk_ns
    last_emitted_offset = None

    def write_boundary():
        nonlocal last_emitted_offset
        offset = int(get_offset())
        if last_emitted_offset == offset:
            return
        json.dump({"offset": offset}, search_output)
        search_output.write("\n")
        search_output.flush()
        last_emitted_offset = offset

    def on_event(code, instruction_offset):
        nonlocal next_emit_ns
        now_ns = time.process_time_ns()
        if now_ns < next_emit_ns:
            return None
        write_boundary()
        while next_emit_ns <= now_ns:
            next_emit_ns += chunk_ns
        return None

    def emit_final():
        try:
            write_boundary()
        except Exception:
            pass

    atexit.register(emit_final)
    _disable = utils.call_counter_disable_for
    sys.monitoring.register_callback(tool_id, E.PY_START, _disable(on_event))
    sys.monitoring.register_callback(tool_id, E.PY_RETURN, _disable(on_event))
    sys.monitoring.register_callback(tool_id, E.PY_UNWIND, _disable(on_event))
    sys.monitoring.set_events(tool_id, E.PY_START | E.PY_RETURN | E.PY_UNWIND)

    return search_output
