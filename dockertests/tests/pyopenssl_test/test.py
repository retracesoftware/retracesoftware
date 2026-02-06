import os
import socket
import subprocess
import threading

from OpenSSL import SSL


def generate_self_signed_cert():
    """
    Generate a self-signed certificate for testing via the `openssl` CLI.

    Note: This requires `openssl` to exist in the container image.
    """
    cert_path = "server.crt"
    key_path = "server.key"

    subprocess.run(["openssl", "genrsa", "-out", key_path, "2048"], check=True, capture_output=True)
    subprocess.run(
        [
            "openssl",
            "req",
            "-new",
            "-x509",
            "-key",
            key_path,
            "-out",
            cert_path,
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
        ],
        check=True,
        capture_output=True,
    )

    return cert_path, key_path


def assert_true(condition, label):
    if not condition:
        print(f"[FAIL] {label}", flush=True)
        raise AssertionError(label)
    print(f"[PASS] {label}", flush=True)


def run_server(port, ready_event, cert_path, key_path):
    context = SSL.Context(SSL.TLS_SERVER_METHOD)
    context.use_privatekey_file(key_path)
    context.use_certificate_file(cert_path)

    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    sock.listen(1)
    ready_event.set()

    conn, _addr = sock.accept()
    ssl_conn = SSL.Connection(context, conn)
    ssl_conn.set_accept_state()
    ssl_conn.do_handshake()
    data = ssl_conn.recv(1024)
    ssl_conn.send(b"Echo: " + data)
    ssl_conn.shutdown()
    ssl_conn.close()
    conn.close()
    sock.close()


def test_pyopenssl_client_server():
    """Test pyOpenSSL client-server communication."""
    print("Testing pyOpenSSL SSL/TLS communication...", flush=True)

    print("1. Generating self-signed certificates...", flush=True)
    cert_path, key_path = generate_self_signed_cert()
    print(f"Generated certificates: {cert_path}, {key_path}", flush=True)

    port = 8443
    while True:
        try:
            test_sock = socket.socket()
            test_sock.bind(("127.0.0.1", port))
            test_sock.close()
            break
        except OSError:
            port += 1

    print(f"2. Using port {port} for SSL server...", flush=True)

    ready = threading.Event()
    server_thread = threading.Thread(target=run_server, args=(port, ready, cert_path, key_path), daemon=True)
    server_thread.start()
    ready.wait(timeout=10)
    print("SSL server started", flush=True)

    print("3. Testing SSL client connection...", flush=True)
    context = SSL.Context(SSL.TLS_CLIENT_METHOD)
    context.set_verify(SSL.VERIFY_NONE, lambda *args: True)
    conn = SSL.Connection(context, socket.socket())
    conn.connect(("127.0.0.1", port))
    conn.set_connect_state()
    conn.do_handshake()

    test_message = b"Hello over SSL"
    conn.send(test_message)
    data = conn.recv(1024)

    expected_response = b"Echo: " + test_message
    assert_true(expected_response in data, "SSL echo client-server communication")

    conn.shutdown()
    conn.close()
    print("SSL client connection successful", flush=True)

    try:
        os.remove(cert_path)
        os.remove(key_path)
        print("Cleaned up certificate files", flush=True)
    except Exception:
        pass


if __name__ == "__main__":
    print("=== pyopenssl_test ===", flush=True)
    test_pyopenssl_client_server()
    print("\nAll pyOpenSSL tests passed.", flush=True)
