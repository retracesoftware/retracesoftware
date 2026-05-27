import _thread
import threading

from retracesoftware.install import install_retrace
from retracesoftware.proxy.io import recorder
from retracesoftware.proxy.taggedtraceio import tagged_trace_writer
from retracesoftware.testing.memorytape import IOMemoryTape


def _configure_system(system):
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})


def test_install_retrace_does_not_patch_thread_lock_aliases():
    tape = IOMemoryTape()

    original_thread_allocate_lock = _thread.allocate_lock
    original_threading_lock = threading.Lock
    original_threading_allocate_lock = threading._allocate_lock

    record_system = recorder(
        writer=tagged_trace_writer(tape.writer().write),
        debug=False,
        stacktraces=False,
    )
    _configure_system(record_system)
    uninstall_record = install_retrace(
        system=record_system,
        monitor_level=0,
        retrace_shutdown=False,
        verbose=False,
    )
    try:
        assert _thread.allocate_lock is original_thread_allocate_lock
        assert threading.Lock is _thread.allocate_lock
        assert threading._allocate_lock is _thread.allocate_lock
    finally:
        uninstall_record()

    assert _thread.allocate_lock is original_thread_allocate_lock
    assert threading.Lock is original_threading_lock
    assert threading._allocate_lock is original_threading_allocate_lock


def test_install_retrace_patches_thread_lock_trylock_methods():
    tape = IOMemoryTape()

    original_lock_acquire = _thread.LockType.acquire
    original_rlock_acquire = _thread.RLock.acquire

    record_system = recorder(
        writer=tagged_trace_writer(tape.writer().write),
        debug=False,
        stacktraces=False,
    )
    _configure_system(record_system)
    uninstall_record = install_retrace(
        system=record_system,
        monitor_level=0,
        retrace_shutdown=False,
        verbose=False,
    )
    try:
        assert _thread.LockType.acquire is not original_lock_acquire
        assert _thread.RLock.acquire is not original_rlock_acquire

        lock = _thread.allocate_lock()
        assert lock.acquire(False)
        lock.release()

        rlock = _thread.RLock()
        assert rlock.acquire(False)
        rlock.release()
    finally:
        uninstall_record()

    assert _thread.LockType.acquire is original_lock_acquire
    assert _thread.RLock.acquire is original_rlock_acquire
