import queue
import threading

from retracesoftware.proxy.messagestream import MemoryWriter


def test_wrapped_queue_condition_delegates_outside_retrace(system):
    writer = MemoryWriter(thread=threading.get_ident)

    with system.record_context(writer):
        q = queue.Queue()
        with q.not_empty:
            pass

    # The queue was created while retrace was active, so its condition still
    # holds a wrapped lock. Outside retrace that wrapped method path must
    # delegate through the live backing lock rather than calling the C
    # descriptor with the proxy object as ``self``.
    with q.not_empty:
        pass
