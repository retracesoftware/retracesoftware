"""Mode types for installing record/replay policies onto a ``System``."""

from .base import Mode
from .record import RecordMode
from .replay import ReplayMode


__all__ = ["Mode", "RecordMode", "ReplayMode"]
