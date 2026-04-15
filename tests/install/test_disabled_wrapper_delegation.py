import queue
import threading

from retracesoftware.install import install_retrace
from retracesoftware.proxy.contexts import record_context
from retracesoftware.proxy.system import System
from retracesoftware.testing.memorytape import MemoryWriter


def test_wrapped_queue_condition_delegates_outside_retrace():
    system = System()
    writer = MemoryWriter(thread=threading.get_ident)
    uninstall = install_retrace(system=system, retrace_shutdown=False)

    try:
        with record_context(system, writer):
            q = queue.Queue()
            with q.not_empty:
                pass

        # The queue was created while retrace was active, so its condition still
        # holds a wrapped lock. Outside retrace that wrapped method path must
        # delegate through the live backing lock rather than calling the C
        # descriptor with the proxy object as ``self``.
        with q.not_empty:
            pass
    finally:
        uninstall()
