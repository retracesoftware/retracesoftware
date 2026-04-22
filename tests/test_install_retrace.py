import socket
import _thread
import threading

from retracesoftware.install import install_retrace
from retracesoftware.proxy.io import recorder, replayer
from retracesoftware.testing.memorytape import IOMemoryTape


def _configure_system(system):
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})


def _new_socket():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.close()


def test_install_retrace_uninstall_resets_patched_types():
    tape = IOMemoryTape()

    record_system = recorder(writer=tape.writer().write, debug=False, stacktraces=False)
    _configure_system(record_system)
    uninstall_record = install_retrace(
        system=record_system,
        monitor_level=0,
        retrace_shutdown=False,
        verbose=False,
    )
    try:
        assert record_system.patched_types
        assert getattr(socket.socket, "__retrace_system__", None) is record_system
    finally:
        uninstall_record()

    assert not record_system.patched_types
    assert getattr(socket.socket, "__retrace_system__", None) is not record_system

    tape_len = len(tape.tape)
    _new_socket()
    assert len(tape.tape) == tape_len

    replay_reader = tape.reader()
    replay_system = replayer(
        next_object=replay_reader.read,
        close=getattr(replay_reader, "close", None),
        debug=False,
        stacktraces=False,
    )
    _configure_system(replay_system)
    uninstall_replay = install_retrace(
        system=replay_system,
        monitor_level=0,
        retrace_shutdown=False,
        verbose=False,
    )
    try:
        assert replay_system.patched_types
        assert getattr(socket.socket, "__retrace_system__", None) is replay_system
    finally:
        uninstall_replay()

    assert not replay_system.patched_types
    assert getattr(socket.socket, "__retrace_system__", None) is not replay_system
    _new_socket()


def test_install_retrace_uninstall_restores_thread_lock_aliases():
    tape = IOMemoryTape()

    original_thread_allocate_lock = _thread.allocate_lock
    original_threading_lock = threading.Lock
    original_threading_allocate_lock = threading._allocate_lock

    record_system = recorder(writer=tape.writer().write, debug=False, stacktraces=False)
    _configure_system(record_system)
    uninstall_record = install_retrace(
        system=record_system,
        monitor_level=0,
        retrace_shutdown=False,
        verbose=False,
    )
    try:
        assert threading.Lock is _thread.allocate_lock
        assert threading._allocate_lock is _thread.allocate_lock
    finally:
        uninstall_record()

    assert _thread.allocate_lock is original_thread_allocate_lock
    assert threading.Lock is original_threading_lock
    assert threading._allocate_lock is original_threading_allocate_lock
    assert threading.Lock is _thread.allocate_lock
    assert threading._allocate_lock is _thread.allocate_lock

    replay_reader = tape.reader()
    replay_system = replayer(
        next_object=replay_reader.read,
        close=getattr(replay_reader, "close", None),
        debug=False,
        stacktraces=False,
    )
    _configure_system(replay_system)
    uninstall_replay = install_retrace(
        system=replay_system,
        monitor_level=0,
        retrace_shutdown=False,
        verbose=False,
    )
    try:
        assert threading.Lock is _thread.allocate_lock
        assert threading._allocate_lock is _thread.allocate_lock
    finally:
        uninstall_replay()

    assert _thread.allocate_lock is original_thread_allocate_lock
    assert threading.Lock is original_threading_lock
    assert threading._allocate_lock is original_threading_allocate_lock
