import _thread
from types import SimpleNamespace

import retracesoftware.control_runtime as control_runtime
import retracesoftware.utils.breakpoint as breakpoint_module
from retracesoftware.control_runtime import _SetTraceNextInstructionMonitor
from retracesoftware.utils.breakpoint import BreakpointSpec


def _code(name, filename=None):
    namespace = {}
    filename = filename or f"{name}.py"
    exec(compile(f"def {name}():\n    return 1\n", filename, "exec"), namespace)
    return namespace[name].__code__


def _frame(code, offset, parent=None):
    return SimpleNamespace(
        f_code=code,
        f_lasti=offset,
        f_lineno=code.co_firstlineno + 1,
        f_locals={},
        f_globals={},
        f_back=parent,
        f_trace=None,
        f_trace_lines=True,
        f_trace_opcodes=True,
    )


def test_next_instruction_arms_stopped_frame_and_existing_callers(monkeypatch):
    installed = object()
    trace_state = {"current": None}

    def settrace(trace):
        trace_state["current"] = installed if trace is not None else None

    monkeypatch.setattr(control_runtime.sys, "settrace", settrace)
    monkeypatch.setattr(
        control_runtime.sys,
        "gettrace",
        lambda: trace_state["current"],
    )

    parent = _frame(_code("parent"), 4)
    target = _frame(_code("target"), 2, parent)
    monitor = _SetTraceNextInstructionMonitor(
        callback=lambda frame: None,
        thread_id=_thread.get_ident(),
    )

    monitor.install(target)

    assert target.f_trace is installed
    assert target.f_trace_opcodes is True
    assert parent.f_trace is installed
    assert parent.f_trace_opcodes is True

    monitor.close()
    assert trace_state["current"] is None


def test_next_instruction_preserves_monitor_installed_by_hit_callback(monkeypatch):
    replacement = object()
    trace_state = {"current": object()}
    target_code = _code("target")

    monkeypatch.setattr(
        control_runtime.sys,
        "gettrace",
        lambda: trace_state["current"],
    )
    monkeypatch.setattr(
        control_runtime.sys,
        "settrace",
        lambda trace: trace_state.__setitem__("current", trace),
    )

    def on_hit(frame):
        trace_state["current"] = replacement

    monitor = _SetTraceNextInstructionMonitor(
        callback=on_hit,
        thread_id=_thread.get_ident(),
        target_code=target_code,
        start_offset=2,
    )
    monitor._installed_trace = trace_state["current"]

    next_frame = _frame(target_code, 4)
    returned_trace = monitor._trace(next_frame, "opcode", None)

    assert returned_trace is replacement


def test_next_instruction_skips_target_start_offset_only_once():
    target_code = _code("target")
    hits = []
    monitor = _SetTraceNextInstructionMonitor(
        callback=hits.append,
        thread_id=_thread.get_ident(),
        target_code=target_code,
        start_offset=2,
    )

    monitor._trace(_frame(target_code, 2), "opcode", None)
    repeated_offset = _frame(target_code, 2)
    monitor._trace(repeated_offset, "opcode", None)

    assert hits == [repeated_offset]


def test_next_instruction_classification_does_not_touch_filesystem(monkeypatch):
    target_code = _code("target", "/tmp/target.py")
    target_frame = _frame(target_code, 4)
    hits = []

    def unexpected_realpath(path):
        raise AssertionError(f"instruction tracing resolved {path!r}")

    monkeypatch.setattr(control_runtime.os.path, "realpath", unexpected_realpath)
    monitor = _SetTraceNextInstructionMonitor(
        callback=hits.append,
        thread_id=_thread.get_ident(),
        target_code=target_code,
        start_offset=2,
    )

    monitor._trace(target_frame, "opcode", None)

    assert hits == [target_frame]


def test_breakpoint_callback_returns_replacement_trace(monkeypatch):
    target_code = _code("target", "/tmp/target.py")
    target_frame = _frame(target_code, 2)
    replacement = object()
    captured = {}

    monkeypatch.setattr(breakpoint_module, "_HAS_MONITORING", False)
    monkeypatch.setattr(breakpoint_module, "install_call_counter", lambda: None)
    monkeypatch.setattr(
        breakpoint_module,
        "cursor_snapshot",
        lambda: SimpleNamespace(to_dict=lambda: {"function_counts": [1]}),
    )
    monkeypatch.setattr(
        breakpoint_module,
        "SetTraceBreakpointMonitor",
        lambda trace: captured.setdefault("trace", trace),
    )
    monkeypatch.setattr(breakpoint_module.sys, "gettrace", lambda: replacement)

    callback_calls = []
    monitor = breakpoint_module.install_breakpoint(
        BreakpointSpec(file="/tmp/target.py", line=target_frame.f_lineno),
        callback=callback_calls.append,
        disable_for=lambda function: function,
    )

    returned_trace = monitor(target_frame, "line", None)

    assert callback_calls == [{"function_counts": [1]}]
    assert returned_trace is replacement


def test_breakpoint_monitor_restores_effective_wrapped_hooks(monkeypatch):
    previous_trace = object()
    previous_thread_trace = object()
    installed_trace = object()
    installed_thread_trace = object()
    trace_state = {"current": previous_trace}
    thread_state = {"current": previous_thread_trace}

    def settrace(trace):
        trace_state["current"] = (
            installed_trace if callable(trace) else trace
        )

    def set_thread_trace(trace):
        thread_state["current"] = (
            installed_thread_trace if callable(trace) else trace
        )

    monkeypatch.setattr(breakpoint_module.sys, "settrace", settrace)
    monkeypatch.setattr(
        breakpoint_module.sys,
        "gettrace",
        lambda: trace_state["current"],
    )
    monkeypatch.setattr(
        breakpoint_module.threading,
        "settrace",
        set_thread_trace,
    )
    monkeypatch.setattr(
        breakpoint_module.threading,
        "gettrace",
        lambda: thread_state["current"],
    )

    monitor = breakpoint_module.SetTraceBreakpointMonitor(lambda *args: None)
    assert trace_state["current"] is installed_trace
    assert thread_state["current"] is installed_thread_trace

    monitor.close()

    assert trace_state["current"] is previous_trace
    assert thread_state["current"] is previous_thread_trace
