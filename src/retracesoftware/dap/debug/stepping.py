"""Stepping state machine for step over / step in / step out.

During replay this drives the execution forward by the right amount and then
fires a stopped event.  The actual sys.monitoring hookup happens when
integrated with the replay engine; this module defines the state machine and
interfaces.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..protocol import types

if TYPE_CHECKING:
    from ..protocol.dispatch import Dispatcher
    from .cursor import Cursor

log = logging.getLogger(__name__)


class StepManager:
    """Manages stepping state.

    In the initial scaffold this sends stopped events immediately (no actual
    execution).  When wired to the replay engine, the ``do_*`` methods will
    configure sys.monitoring hooks and resume execution; the stopped event
    will be sent when the target position is reached.
    """

    def __init__(self, cursor: Cursor) -> None:
        self.cursor = cursor
        self._stepping = False

    def do_continue(self, dispatcher: Dispatcher, thread_id: int) -> None:
        """Resume until next breakpoint or end."""
        log.debug("continue thread=%d", thread_id)
        # Placeholder: immediately send stopped (will be async in real replay)
        self.cursor.advance(thread_id, self.cursor.source, self.cursor.line, self.cursor.function)
        dispatcher.send(types.stopped_event("breakpoint", thread_id=thread_id))

    def do_next(self, dispatcher: Dispatcher, thread_id: int, granularity: str = "statement") -> None:
        """Step to the next line in the same frame."""
        log.debug("next thread=%d granularity=%s", thread_id, granularity)
        self.cursor.advance(thread_id, self.cursor.source, self.cursor.line + 1, self.cursor.function)
        dispatcher.send(types.stopped_event("step", thread_id=thread_id))

    def do_step_in(self, dispatcher: Dispatcher, thread_id: int, granularity: str = "statement") -> None:
        """Step into the next call."""
        log.debug("stepIn thread=%d granularity=%s", thread_id, granularity)
        self.cursor.advance(thread_id, self.cursor.source, self.cursor.line + 1, self.cursor.function)
        dispatcher.send(types.stopped_event("step", thread_id=thread_id))

    def do_step_out(self, dispatcher: Dispatcher, thread_id: int, granularity: str = "statement") -> None:
        """Step until the current frame returns."""
        log.debug("stepOut thread=%d granularity=%s", thread_id, granularity)
        self.cursor.advance(thread_id, self.cursor.source, self.cursor.line + 1, self.cursor.function)
        dispatcher.send(types.stopped_event("step", thread_id=thread_id))

    def do_pause(self, dispatcher: Dispatcher) -> None:
        """Pause execution."""
        log.debug("pause")
        dispatcher.send(types.stopped_event("pause", thread_id=0))
