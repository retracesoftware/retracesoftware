"""Breakpoint management via sys.monitoring.

Manages source breakpoints (file + line), function breakpoints, and
exception break filters.  Uses sys.monitoring LINE and CALL events.
"""

from __future__ import annotations

import logging
import os
from typing import Any

log = logging.getLogger(__name__)


_norm_cache: dict[str, str] = {}

def _normalize(path: str) -> str:
    """Canonical absolute path for reliable breakpoint matching.

    Cached per input string — co_filename values are interned so the
    cache stays small and turns an os.path.realpath syscall into a
    dict lookup on the hot path.
    """
    if not path:
        return path
    r = _norm_cache.get(path)
    if r is not None:
        return r
    r = os.path.realpath(path)
    _norm_cache[path] = r
    return r

_next_bp_id = 0


def _alloc_id() -> int:
    global _next_bp_id
    _next_bp_id += 1
    return _next_bp_id


class BreakpointManager:

    def __init__(self) -> None:
        # path -> {line -> bp_info}
        self._source_bps: dict[str, dict[int, dict[str, Any]]] = {}
        self._function_bps: list[dict[str, Any]] = []
        self._exception_filters: list[str] = []

    # -- source breakpoints -------------------------------------------------

    def set_breakpoints(
        self, path: str, specs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace all breakpoints for *path* with *specs*.

        Each spec: {"line": int, "condition"?: str, "hitCondition"?: str}
        Returns DAP breakpoint objects.
        """
        norm = _normalize(path)
        log.debug("set_breakpoints: raw=%s normalized=%s", path, norm)

        new_bps: dict[int, dict[str, Any]] = {}
        result: list[dict[str, Any]] = []

        for spec in specs:
            line = spec.get("line", 0)
            bp_id = _alloc_id()
            bp = {
                "id": bp_id,
                "verified": True,
                "line": line,
                "source": {"path": path},
            }
            new_bps[line] = {
                "id": bp_id,
                "line": line,
                "condition": spec.get("condition"),
                "hit_condition": spec.get("hitCondition"),
            }
            result.append(bp)

        self._source_bps[norm] = new_bps
        return result

    # -- function breakpoints -----------------------------------------------

    def set_function_breakpoints(
        self, specs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace all function breakpoints with *specs*."""
        self._function_bps = []
        result: list[dict[str, Any]] = []

        for spec in specs:
            name = spec.get("name", "")
            bp_id = _alloc_id()
            self._function_bps.append({
                "id": bp_id,
                "name": name,
                "condition": spec.get("condition"),
            })
            result.append({"id": bp_id, "verified": True})

        return result

    # -- exception filters --------------------------------------------------

    def set_exception_filters(self, filters: list[str]) -> None:
        self._exception_filters = list(filters)

    @property
    def break_on_raised(self) -> bool:
        return "raised" in self._exception_filters

    @property
    def break_on_uncaught(self) -> bool:
        return "uncaught" in self._exception_filters

    # -- queries ------------------------------------------------------------

    def has_breakpoint(self, path: str, line: int) -> bool:
        bps = self._source_bps.get(_normalize(path))
        return bps is not None and line in bps

    def get_breakpoint(self, path: str, line: int) -> dict[str, Any] | None:
        bps = self._source_bps.get(_normalize(path))
        if bps is None:
            return None
        return bps.get(line)

    def has_function_breakpoint(self, name: str) -> bool:
        return any(bp["name"] == name for bp in self._function_bps)

