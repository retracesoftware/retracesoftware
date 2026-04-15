"""Test record/replay of socket operations.

Verifies that _socket C extension calls (socket creation,
connect, send, recv) are correctly captured during record and
replayed from the stored event stream.

The server/client tests are structured so that the *peer* runs
outside the recording context.  This means:

- **test_server_recv**: during replay only the server code runs;
  ``accept()`` and ``recv()`` are satisfied from the recorded
  stream with no real client.
- **test_client_recv**: during replay only the client code runs;
  ``connect()`` and ``recv()`` are satisfied from the recorded
  stream with no real server.

Symmetric tests (echo, multi-exchange, large payload) use the
single-threaded loopback helper and ``runner.run()`` which records
and replays the same code path.
"""
import socket
import threading

from tests.runner import Runner, retrace_test


# ── helpers ───────────────────────────────────────────────────────

def _loopback_pair():
    """Create a connected (client, server_conn) socket pair over TCP.

    Returns (client, conn, srv) — caller must close all three.
    On localhost the kernel completes the three-way handshake in the
    listen backlog, so connect() then accept() works single-threaded.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)

    cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    cli.connect(("127.0.0.1", port))

    conn, addr = srv.accept()
    return cli, conn, srv


# ── name resolution ───────────────────────────────────────────────

@retrace_test
def test_gethostname():
    """socket.gethostname() records and replays identically."""
    value = socket.gethostname()
    assert isinstance(value, str)
    assert value
    return value


@retrace_test
def test_getaddrinfo():
    """socket.getaddrinfo records and replays identically."""
    value = socket.getaddrinfo("localhost", 80, socket.AF_INET, socket.SOCK_STREAM)
    assert value
    return value


# ── TCP server / client (peer absent during replay) ──────────────

def test_server_recv():
    """Server side receives bytes — no client during replay.

    All sockets are created inside the context so they are bound and
    their operations go through the gate.  A real client connects
    during record; during replay the gate returns recorded results
    with no real client needed.
    """
    ready = threading.Event()
    port_box = []

    def client():
        ready.wait(timeout=2.0)
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.connect(("127.0.0.1", port_box[0]))
        c.sendall(b"hello from client")
        c.close()

    t = threading.Thread(target=client)
    t.start()
    runner = Runner()

    def server_work():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port_box.append(srv.getsockname()[1])
        srv.listen(1)
        ready.set()
        conn, _addr = srv.accept()
        data = conn.recv(4096)
        conn.close()
        srv.close()
        return data

    recording = runner.record(server_work)
    t.join()

    result = runner.replay(recording, server_work)
    assert result == b"hello from client"


def test_client_recv():
    """Client side receives bytes — no server during replay.

    A real server runs *outside* the recording context.  All client
    sockets are created inside the context so the gate intercepts
    connect/recv/close.  During replay the gate returns recorded
    results with no real server needed.
    """
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    srv.listen(1)

    def server():
        conn, _addr = srv.accept()
        conn.sendall(b"hello from server")
        conn.close()

    t = threading.Thread(target=server)
    t.start()
    runner = Runner()

    def client_work():
        cli = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        cli.connect(("127.0.0.1", port))
        data = cli.recv(4096)
        cli.close()
        return data

    recording = runner.record(client_work)
    t.join()
    srv.close()

    result = runner.replay(recording, client_work)
    assert result == b"hello from server"


# ── TCP symmetric (same code records and replays) ────────────────

@retrace_test
def test_echo_round_trip():
    """Client sends bytes, reads them back from server."""
    cli, conn, srv = _loopback_pair()
    cli.sendall(b"ping")
    echo = conn.recv(4096)
    conn.sendall(echo)
    reply = cli.recv(4096)
    conn.close()
    cli.close()
    srv.close()
    assert reply == b"ping"
    return reply


@retrace_test
def test_multiple_exchanges():
    """Multiple send/recv round trips over one connection."""
    cli, conn, srv = _loopback_pair()
    replies = []
    for msg in [b"aaa", b"bbb", b"ccc"]:
        cli.sendall(msg)
        chunk = conn.recv(4096)
        conn.sendall(chunk.upper())
        replies.append(cli.recv(4096))
    conn.close()
    cli.close()
    srv.close()
    assert replies == [b"AAA", b"BBB", b"CCC"]
    return replies


@retrace_test
def test_large_payload():
    """Sending a payload larger than a typical buffer size."""
    cli, conn, srv = _loopback_pair()
    payload = b"x" * 100_000
    cli.sendall(payload)

    received = b""
    while len(received) < len(payload):
        chunk = conn.recv(65536)
        if not chunk:
            break
        received += chunk

    conn.close()
    cli.close()
    srv.close()
    assert len(received) == 100_000
    return len(received)


# ── recv_into edge case ──────────────────────────────────────────

@retrace_test
def test_recv_into():
    """recv_into fills a pre-allocated buffer via the recv proxy."""
    cli, conn, srv = _loopback_pair()
    cli.sendall(b"recv_into test")
    buf = bytearray(4096)
    nbytes = conn.recv_into(buf)
    conn.close()
    cli.close()
    srv.close()
    value = bytes(buf[:nbytes])
    assert value == b"recv_into test"
    return value


@retrace_test
def test_recv_into_with_nbytes():
    """recv_into respects an explicit nbytes limit."""
    cli, conn, srv = _loopback_pair()
    cli.sendall(b"abcdefghij")
    buf = bytearray(4096)
    nbytes = conn.recv_into(buf, 5)
    conn.close()
    cli.close()
    srv.close()
    value = bytes(buf[:nbytes])
    assert value == b"abcde"
    return value


@retrace_test
def test_recv_into_multiple():
    """recv_into works across multiple sequential reads."""
    cli, conn, srv = _loopback_pair()
    parts = []
    for msg in [b"one", b"two", b"three"]:
        cli.sendall(msg)
        buf = bytearray(64)
        n = conn.recv_into(buf)
        parts.append(bytes(buf[:n]))
    conn.close()
    cli.close()
    srv.close()
    assert parts == [b"one", b"two", b"three"]
    return parts


def test_recv_into_server_only():
    """recv_into replays correctly with no client during replay.

    Server socket is created inside the context so accept/recv_into
    go through the gate.  A real client connects during record; during
    replay the gate returns recorded results.
    """
    ready = threading.Event()
    port_box = []

    def client():
        ready.wait(timeout=2.0)
        c = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        c.connect(("127.0.0.1", port_box[0]))
        c.sendall(b"hello via recv_into")
        c.close()

    t = threading.Thread(target=client)
    t.start()
    runner = Runner()

    def server_work():
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", 0))
        port_box.append(srv.getsockname()[1])
        srv.listen(1)
        ready.set()
        conn, _addr = srv.accept()
        buf = bytearray(4096)
        nbytes = conn.recv_into(buf)
        conn.close()
        srv.close()
        return bytes(buf[:nbytes])

    recording = runner.record(server_work)
    t.join()

    result = runner.replay(recording, server_work)
    assert result == b"hello via recv_into"
