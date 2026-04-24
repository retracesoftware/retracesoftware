"""Gate discipline for control-plane I/O.

All DAP socket reads/writes and trace file operations must bypass retrace's
interception mechanism.  This module provides a context manager that disables
the gates for the duration of a block, and wrappers for socket I/O.

When running outside of retrace (e.g. standalone testing), the gate operations
are no-ops.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

log = logging.getLogger(__name__)

def _try_import_gates():
    """Return a process-global gate handle if one exists.

    The legacy proxy gateway/record-system stack used to expose global gate
    objects here. The current runtime uses per-System gates and does not publish
    a process-global handle, so DAP replay falls back to a no-op shim.
    """
    log.debug("No process-global retrace gates available")
    return None


@contextmanager
def disabled() -> Generator[None, None, None]:
    """Context manager: disable retrace gates for the enclosed block.

    If retrace is not active (e.g. standalone testing), this is a no-op.
    """
    gates = _try_import_gates()
    if gates is None:
        yield
        return

    try:
        gates.disable()
        yield
    finally:
        gates.enable()


def wrap(fn):
    """Return a wrapper that calls *fn* with gates disabled."""
    def wrapper(*args, **kwargs):
        with disabled():
            return fn(*args, **kwargs)
    wrapper.__name__ = fn.__name__
    wrapper.__qualname__ = fn.__qualname__
    return wrapper
