"""Global replay cursor — monotonic line counter."""

from __future__ import annotations

from typing import Any


class Cursor:
    """Tracks the replay position as a global monotonic counter.

    The counter increments on every LINE event across all threads, giving a
    total ordering of the deterministic replay.
    """

    __slots__ = ("counter", "thread_id", "source", "line", "function")

    def __init__(self) -> None:
        self.counter: int = 0
        self.thread_id: int = 0
        self.source: str = ""
        self.line: int = 0
        self.function: str = ""

    def advance(self, thread_id: int, source: str, line: int, function: str) -> int:
        """Increment the counter and update position fields. Returns new counter."""
        self.counter += 1
        self.thread_id = thread_id
        self.source = source
        self.line = line
        self.function = function
        return self.counter

    def reset(self) -> None:
        self.counter = 0
        self.thread_id = 0
        self.source = ""
        self.line = 0
        self.function = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "counter": self.counter,
            "thread_id": self.thread_id,
            "source": self.source,
            "line": self.line,
            "function": self.function,
        }
