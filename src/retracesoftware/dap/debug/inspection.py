"""Frame / variable / scope inspection.

Provides stack trace, scopes, variables, evaluate, and exceptionInfo
responses.  When connected to the replay engine these read live interpreter
state via sys._getframe().  The scaffold returns placeholder data.
"""

from __future__ import annotations

import os
import sys
import logging
from typing import Any

log = logging.getLogger(__name__)

_next_ref = 0


def _alloc_ref() -> int:
    global _next_ref
    _next_ref += 1
    return _next_ref


class Inspector:
    """Inspects replay state: frames, scopes, variables."""

    def __init__(self) -> None:
        # ref -> (frame_locals dict | container object)
        self._refs: dict[int, Any] = {}
        self._frame_cache: dict[int, dict[str, Any]] = {}

    def invalidate(self) -> None:
        """Clear all cached references (call on resume)."""
        self._refs.clear()
        self._frame_cache.clear()

    # -- stackTrace ---------------------------------------------------------

    def stack_trace(
        self, thread_id: int, start_frame: int = 0, levels: int = 0
    ) -> dict[str, Any]:
        frames = self._capture_frames(thread_id)

        if levels > 0:
            frames = frames[start_frame : start_frame + levels]
        elif start_frame > 0:
            frames = frames[start_frame:]

        dap_frames = []
        for i, f in enumerate(frames):
            frame_id = _alloc_ref()
            self._frame_cache[frame_id] = f
            dap_frames.append({
                "id": frame_id,
                "name": f.get("name", "<unknown>"),
                "source": {"path": f.get("source", ""), "name": f.get("source_name", "")},
                "line": f.get("line", 0),
                "column": 1,
            })

        return {
            "stackFrames": dap_frames,
            "totalFrames": len(dap_frames),
        }

    # -- scopes -------------------------------------------------------------

    def scopes(self, frame_id: int) -> dict[str, Any]:
        frame = self._frame_cache.get(frame_id)
        result: list[dict[str, Any]] = []

        if frame is not None:
            locals_ref = _alloc_ref()
            self._refs[locals_ref] = frame.get("locals", {})
            result.append({
                "name": "Locals",
                "variablesReference": locals_ref,
                "expensive": False,
            })

            globals_ref = _alloc_ref()
            self._refs[globals_ref] = frame.get("globals", {})
            result.append({
                "name": "Globals",
                "variablesReference": globals_ref,
                "expensive": True,
            })

        return {"scopes": result}

    # -- variables ----------------------------------------------------------

    def variables(
        self, ref: int, start: int = 0, count: int = 0
    ) -> dict[str, Any]:
        obj = self._refs.get(ref)
        if obj is None:
            return {"variables": []}

        items = self._expand(obj)

        if count > 0:
            items = items[start : start + count]
        elif start > 0:
            items = items[start:]

        return {"variables": items}

    # -- evaluate -----------------------------------------------------------

    def evaluate(self, expression: str, frame_id: int | None = None) -> dict[str, Any]:
        frame = self._frame_cache.get(frame_id) if frame_id is not None else None
        local_ns = frame.get("locals", {}) if frame else {}
        global_ns = frame.get("globals", {}) if frame else {}

        try:
            value = eval(expression, global_ns, local_ns)  # noqa: S307
        except Exception as exc:
            return {"result": str(exc), "type": type(exc).__name__, "variablesReference": 0}

        ref = 0
        if _has_children(value):
            ref = _alloc_ref()
            self._refs[ref] = value

        return {
            "result": _repr(value),
            "type": type(value).__name__,
            "variablesReference": ref,
        }

    # -- exceptionInfo ------------------------------------------------------

    def exception_info(self, thread_id: int) -> dict[str, Any]:
        exc = sys.exc_info()[1]
        if exc is None:
            return {
                "exceptionId": "",
                "description": "No active exception",
                "breakMode": "never",
            }
        return {
            "exceptionId": type(exc).__qualname__,
            "description": str(exc),
            "breakMode": "always",
        }

    # -- stack from a live paused frame ---------------------------------------

    def stack_trace_from_frame(
        self,
        frame: Any,
        thread_id: int,
        start_frame: int = 0,
        levels: int = 0,
    ) -> dict[str, Any]:
        """Build a stackTrace response walking a real paused FrameType."""
        raw = self._walk_frame(frame)

        if levels > 0:
            raw = raw[start_frame : start_frame + levels]
        elif start_frame > 0:
            raw = raw[start_frame:]

        dap_frames = []
        for f in raw:
            frame_id = _alloc_ref()
            self._frame_cache[frame_id] = f
            dap_frames.append({
                "id": frame_id,
                "name": f.get("name", "<unknown>"),
                "source": {"path": f.get("source", ""), "name": f.get("source_name", "")},
                "line": f.get("line", 0),
                "column": 1,
            })

        return {
            "stackFrames": dap_frames,
            "totalFrames": len(dap_frames),
        }

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _walk_frame(frame) -> list[dict[str, Any]]:
        """Walk a FrameType chain into serialisable dicts, skipping adapter frames."""
        frames: list[dict[str, Any]] = []
        f = frame
        while f is not None:
            fn = f.f_code.co_filename
            if "retracesoftware/dap" not in fn:
                abspath = os.path.abspath(fn) if not os.path.isabs(fn) else fn
                frames.append({
                    "name": f.f_code.co_name,
                    "source": abspath,
                    "source_name": os.path.basename(abspath),
                    "line": f.f_lineno,
                    "locals": dict(f.f_locals),
                    "globals": dict(f.f_globals),
                })
            f = f.f_back
        return frames

    def _capture_frames(self, thread_id: int) -> list[dict[str, Any]]:
        """Fallback: capture from the current thread's stack."""
        frames: list[dict[str, Any]] = []
        try:
            f = sys._getframe(0)
            while f is not None:
                module = f.f_globals.get("__name__", "")
                if not module.startswith("retracesoftware.dap"):
                    fn = f.f_code.co_filename
                    abspath = os.path.abspath(fn) if not os.path.isabs(fn) else fn
                    frames.append({
                        "name": f.f_code.co_name,
                        "source": abspath,
                        "source_name": os.path.basename(abspath),
                        "line": f.f_lineno,
                        "locals": dict(f.f_locals),
                        "globals": dict(f.f_globals),
                    })
                f = f.f_back
        except (AttributeError, ValueError):
            pass

        if not frames:
            frames.append({
                "name": "<main>",
                "source": "",
                "source_name": "",
                "line": 1,
                "locals": {},
                "globals": {},
            })

        return frames

    def _expand(self, obj: Any) -> list[dict[str, Any]]:
        """Expand an object into DAP variable entries."""
        if isinstance(obj, dict):
            return [self._var_entry(str(k), v) for k, v in obj.items()]
        if isinstance(obj, (list, tuple)):
            return [self._var_entry(f"[{i}]", v) for i, v in enumerate(obj)]
        if isinstance(obj, set):
            return [self._var_entry(f"{{{i}}}", v) for i, v in enumerate(obj)]

        # Object attributes
        entries: list[dict[str, Any]] = []
        for attr in dir(obj):
            if attr.startswith("_"):
                continue
            try:
                val = getattr(obj, attr)
                if not callable(val):
                    entries.append(self._var_entry(attr, val))
            except Exception:
                pass
        return entries

    def _var_entry(self, name: str, value: Any) -> dict[str, Any]:
        ref = 0
        if _has_children(value):
            ref = _alloc_ref()
            self._refs[ref] = value
        return {
            "name": name,
            "value": _repr(value),
            "type": type(value).__name__,
            "variablesReference": ref,
        }


def _has_children(value: Any) -> bool:
    return isinstance(value, (dict, list, tuple, set)) and len(value) > 0


def _repr(value: Any) -> str:
    try:
        r = repr(value)
        if len(r) > 200:
            return r[:197] + "..."
        return r
    except Exception:
        return "<error>"
