import _thread
from contextlib import contextmanager
import queue
import threading

from retracesoftware.threadid import ThreadId


def _wait_for(result_queue: queue.Queue, count: int):
    return [result_queue.get(timeout=5) for _ in range(count)]


@contextmanager
def _patched_thread_start(thread_id: ThreadId):
    original = _thread.start_new_thread
    patched = thread_id.wrap_start_new_thread(original)

    _thread.start_new_thread = patched
    threading._start_new_thread = patched
    try:
        yield
    finally:
        _thread.start_new_thread = original
        threading._start_new_thread = original


def test_threadid_starts_at_root_id():
    thread_id = ThreadId()

    assert thread_id() == ()


def test_threadid_wrap_start_new_thread_assigns_child_ids():
    thread_id = ThreadId()
    results = queue.Queue()

    with _patched_thread_start(thread_id):
        def worker(label):
            results.put((label, thread_id()))

        native_ids = [
            _thread.start_new_thread(worker, ("first",)),
            _thread.start_new_thread(worker, ("second",)),
        ]

        seen = sorted(_wait_for(results, 2))

        assert all(isinstance(native_id, int) for native_id in native_ids)
        assert seen == [
            ("first", (0,)),
            ("second", (1,)),
        ]
        assert thread_id() == ()


def test_threadid_wrap_start_new_thread_assigns_nested_child_lineage():
    thread_id = ThreadId()
    results = queue.Queue()

    with _patched_thread_start(thread_id):
        def grandchild():
            results.put(("grandchild", thread_id()))

        def child():
            results.put(("child", thread_id()))
            _thread.start_new_thread(grandchild, ())

        _thread.start_new_thread(child, ())

        seen = sorted(_wait_for(results, 2))
        assert seen == [
            ("child", (0,)),
            ("grandchild", (0, 0)),
        ]


def test_threadid_wrap_start_new_thread_updates_threading_thread_start():
    thread_id = ThreadId()
    results = queue.Queue()

    with _patched_thread_start(thread_id):
        thread = threading.Thread(target=lambda: results.put(thread_id()))
        thread.start()
        thread.join(timeout=5)

        assert not thread.is_alive()
        assert results.get(timeout=5) == (0,)
        assert thread_id() == ()
