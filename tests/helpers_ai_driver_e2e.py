"""Shared helpers for flaky DAP-backed ai_driver end-to-end tests."""

from __future__ import annotations

import json
from typing import Any

from retracesoftware.ai_driver import DAPExecutor, _initial_observation


def assert_prepositioned_application_stack(
    *,
    trace_path: str,
    task: str,
    path_substring: str,
    attempts: int = 3,
) -> None:
    last_failure: str | None = None
    for _ in range(attempts):
        executor = DAPExecutor(trace_path)
        try:
            transcript: list[dict[str, Any]] = []
            observation = _initial_observation(
                type("Args", (), {"task": task, "trace": trace_path})(),
                executor,
                transcript,
            )
            if "pre-positioned" not in observation["summary"].lower():
                last_failure = observation["summary"]
                continue

            stack_step = next(
                item for item in reversed(transcript) if item["tool"] == "get_stack_trace"
            )
            result = stack_step["result"]
            if result.get("ok") is not True:
                last_failure = json.dumps(result, indent=2)
                continue

            frames = result.get("data", {}).get("stack_frames", [])
            paths = [
                frame.get("source", {}).get("path", "")
                for frame in frames
                if isinstance(frame, dict)
            ]
            if not frames or not any(path_substring in path for path in paths):
                last_failure = f"expected application frames containing {path_substring!r}, got {paths!r}"
                continue
            if any("_pytest" in path for path in paths):
                last_failure = f"unexpected pytest frames in application stack: {paths!r}"
                continue
            if any("<frozen runpy>" in path for path in paths):
                last_failure = f"unexpected runpy frames in application stack: {paths!r}"
                continue
            return
        finally:
            executor.close()

    raise AssertionError(last_failure or "DAP pre-positioning did not reach application stack")
