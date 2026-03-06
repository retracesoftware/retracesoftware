"""Tests for select module record/replay.

select.select() is a blocking wait that returns which fds are ready.
It should record the return value (lists of ready fds) and replay
it without needing real file descriptors.

kqueue/kevent tests exercise the macOS kernel event interface.
During recording, real kqueue syscalls execute; during replay the
proxy replays the recorded control() results.
"""
import sys
import socket
import select
import threading

import pytest

needs_kqueue = pytest.mark.skipif(
    not hasattr(select, "kqueue"), reason="kqueue not available")


def test_select_readable(runner):
    """select.select() detects a readable socket and replays."""
    ready = threading.Event()
    port_box = []

    def client():
        ready.wait(timeout=2.0)
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.connect(("127.0.0.1", port_box[0]))
        c.sendall(b"ping")
        c.close()

    t = threading.Thread(target=client)
    t.start()

    def work():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port_box.append(srv.getsockname()[1])
        srv.listen(1)
        ready.set()
        conn, _ = srv.accept()
        readable, _, _ = select.select([conn], [], [], 5.0)
        assert len(readable) == 1
        data = conn.recv(1024)
        conn.close()
        srv.close()
        return data

    recording = runner.record(work)
    t.join()

    assert recording.result == b"ping"

    result = runner.replay(recording, work)
    assert result == b"ping"


def test_select_timeout(runner):
    """select.select() with timeout returns empty lists and replays."""
    def work():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        readable, writable, errors = select.select([srv], [], [], 0.0)
        srv.close()
        return (len(readable), len(writable), len(errors))

    recording = runner.record(work)
    assert recording.result == (0, 0, 0)

    result = runner.replay(recording, work)
    assert result == (0, 0, 0)


def test_select_writable(runner):
    """select.select() detects a writable socket and replays."""
    def work():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        conn, _ = srv.accept()

        _, writable, _ = select.select([], [conn], [], 1.0)
        assert len(writable) == 1
        conn.sendall(b"hello")
        data = cli.recv(1024)
        conn.close()
        cli.close()
        srv.close()
        return data

    recording = runner.record(work)
    assert recording.result == b"hello"

    result = runner.replay(recording, work)
    assert result == b"hello"


# ── kqueue / kevent ──────────────────────────────────────────────

@needs_kqueue
def test_kqueue_readable(runner):
    """kqueue detects a readable socket and replays."""
    ready = threading.Event()
    port_box = []

    def client():
        ready.wait(timeout=2.0)
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.connect(("127.0.0.1", port_box[0]))
        c.sendall(b"kq-ping")
        c.close()

    t = threading.Thread(target=client)
    t.start()

    def work():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port_box.append(srv.getsockname()[1])
        srv.listen(1)
        ready.set()
        conn, _ = srv.accept()
        kq = select.kqueue()
        ev = select.kevent(
            conn.fileno(),
            filter=select.KQ_FILTER_READ,
            flags=select.KQ_EV_ADD)
        events = kq.control([ev], 1, 5.0)
        assert len(events) == 1
        data = conn.recv(1024)
        kq.close()
        conn.close()
        srv.close()
        return data

    recording = runner.record(work)
    t.join()

    assert recording.result == b"kq-ping"

    result = runner.replay(recording, work)
    assert result == b"kq-ping"


@needs_kqueue
def test_kqueue_writable(runner):
    """kqueue detects a writable socket and replays."""
    def work():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        conn, _ = srv.accept()

        kq = select.kqueue()
        ev = select.kevent(
            conn.fileno(),
            filter=select.KQ_FILTER_WRITE,
            flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT)
        events = kq.control([ev], 1, 1.0)
        assert len(events) == 1
        conn.sendall(b"kq-hello")
        data = cli.recv(1024)
        kq.close()
        conn.close()
        cli.close()
        srv.close()
        return data

    recording = runner.record(work)
    assert recording.result == b"kq-hello"

    result = runner.replay(recording, work)
    assert result == b"kq-hello"


@needs_kqueue
def test_kqueue_timeout(runner):
    """kqueue.control() with no events times out and replays."""
    def work():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        kq = select.kqueue()
        ev = select.kevent(
            srv.fileno(),
            filter=select.KQ_FILTER_READ,
            flags=select.KQ_EV_ADD)
        events = kq.control([ev], 1, 0.0)
        kq.close()
        srv.close()
        return len(events)

    recording = runner.record(work)
    assert recording.result == 0

    result = runner.replay(recording, work)
    assert result == 0


@needs_kqueue
def test_kevent_multiple_filters(runner):
    """Register both read and write kevents, get results back."""
    def work():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        conn, _ = srv.accept()
        cli.sendall(b"multi")

        kq = select.kqueue()
        fd = conn.fileno()
        changes = [
            select.kevent(fd, filter=select.KQ_FILTER_READ,
                          flags=select.KQ_EV_ADD),
            select.kevent(fd, filter=select.KQ_FILTER_WRITE,
                          flags=select.KQ_EV_ADD),
        ]
        events = kq.control(changes, 4, 1.0)
        filters = sorted(e.filter for e in events)
        data = conn.recv(1024)
        kq.close()
        conn.close()
        cli.close()
        srv.close()
        return (filters, data)

    recording = runner.record(work)

    filters, data = recording.result
    assert select.KQ_FILTER_READ in filters
    assert select.KQ_FILTER_WRITE in filters
    assert data == b"multi"

    result_filters, result_data = runner.replay(recording, work)
    assert result_filters == filters
    assert result_data == b"multi"
