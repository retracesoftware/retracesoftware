import os
import socket
import tempfile
import threading

from OpenSSL import SSL


KEY_PEM = b"""-----BEGIN PRIVATE KEY-----
MIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQDmELBcy0nCGHCu
8/iy8A5T5gkSYEoQOvLthu3opVfDJKUjRfWvd4bO8pQeRd6uLK7UXxdgSkW96EEZ
obgS1qJ3F1cNRMdpjnptaHQe3qx9cYd3bND7TewB+XRAujRwGUNcumBh3HpvMqup
lhZgtD/PuPwfApjwRHt19Mgw9ENDnYY79AkjUHvcUJRLDmlwGY0Qzbf9lIatNYfe
aylv2Rkv3xFON4pvzohygNwX8VFAAwzYW/aFcjp6lTv4z62UxKX0vqQn8/WFpWxY
1bRl+hx+uMpql+YqcsLPfSJS//IRQxdo5qgOoOQAQXqqdNswttBNLeMHCz3KQDE3
AsCsKzXpAgMBAAECggEABfGG27bXUL+0LGRRRWWSqAqj9Fs4UONz1Lyj1zZrb1vM
fuvbjBGzEe9yg/+yO4OJ20djnP7+NYjTR2685ElpWutC8RqY118Mj1iWiRjESLh8
nIAkYIrITJGdtgPLv28NXBhChHSxnnY5SzgPm4HO3jZq+0k/rYFYjJkPo8XV9Zc9
vOd+CL/RKJypbGp6j33mWC5pqyqsEQ8ghOA6J0MrLXIFgrqHLmYLKrF+vDgTSvRH
BPYFh4Uix3c/C26qWhJfmK8E5fYEaNHyJe9bdMxltiX+dDGixdFXqy072/Gom6bv
/SXLFFCsmOuLfOVWjYew3V7FR32lO9SXnTFoguIPSwKBgQD0YvP+XLBA5/fFBV/P
16ylPG9YYZYEjK9J8VxhBi3BAcTu4O/I14es8C5OLgKN6ByRJEOPZ4aLFj5hNVna
YVJcaZ8vXHeL/yKv5AOwfNn2snCam5LbvhMUHVcXMUfOyhkIBsfb2VQHMrira/+a
4gWvbC9N6Pe02PuO2baVDz4EfwKBgQDw/4L+lsgIWBfqfPDBaOu54i/CqGrBzZ0K
VRpr0r15+ohpku9m0YRSr+sm7sn/Ogbd16TO9KY0Ye1NKnluu6BCqnKrzcyaQmz9
NiJt5y6blxS382Gw38TA/R4Zjj+/48BTQuAh3unQsTdQPb017/q829J+nQCULa3T
s0wQWiXxlwKBgQDPUvtHeP6VsbUC0fJccs2mSET1p6QLLAaxJi+GqCU8rfGR7gW+
Twps7j16WZIVLSq+/xLJn7wGVtKIySf3GcUzXO+M0Fciz0lwCnIO0XxfyzW4E+9c
uD2bPODbbhVLGyxtIMOAgTjF+oOr+a0YilLkZVUkNVWfeMzAfXZlsk6cpQKBgQDJ
JquCrf2mIUlM+h3FgTqHu0fb9NCulF0IW8IizxJBdqBXZkIWEricf6MJqvPE6P0E
O1KfPspfHIGCD/qtN0PrgPMXfT3SX7Eyo/WWwAhB65dqdmVKyWsjHeH6uKVzF7jW
hhInkzSbcN9XRUDhfT1OVzhZX9g01e+prJTHbUcQXwKBgHIvM83EUBBWO7UaISIj
0aeiC2VjlfXBqLFi6uTS0xIjxGqhpFGOopBFFB44Sn2mxtXuh6/8/E0Tcn3ZFPe2
IoWJxykdeNvKpCBRxuLJzv2jIf0GcI+cDVC+9PjYiPBU63tIHuLxzfKmLRaAacmg
O56SEr2V3q0LyeDez8exQuJS
-----END PRIVATE KEY-----"""

CERT_PEM = b"""-----BEGIN CERTIFICATE-----
MIIDCTCCAfGgAwIBAgIUbgYJEOtiHjJ5pYjqPEN7flMS9RkwDQYJKoZIhvcNAQEL
BQAwFDESMBAGA1UEAwwJbG9jYWxob3N0MB4XDTI2MDQyODE3NDMzOFoXDTM2MDQy
NTE3NDMzOFowFDESMBAGA1UEAwwJbG9jYWxob3N0MIIBIjANBgkqhkiG9w0BAQEF
AAOCAQ8AMIIBCgKCAQEA5hCwXMtJwhhwrvP4svAOU+YJEmBKEDry7Ybt6KVXwySl
I0X1r3eGzvKUHkXeriyu1F8XYEpFvehBGaG4EtaidxdXDUTHaY56bWh0Ht6sfXGH
d2zQ+03sAfl0QLo0cBlDXLpgYdx6bzKrqZYWYLQ/z7j8HwKY8ER7dfTIMPRDQ52G
O/QJI1B73FCUSw5pcBmNEM23/ZSGrTWH3mspb9kZL98RTjeKb86IcoDcF/FRQAMM
2Fv2hXI6epU7+M+tlMSl9L6kJ/P1haVsWNW0ZfocfrjKapfmKnLCz30iUv/yEUMX
aOaoDqDkAEF6qnTbMLbQTS3jBws9ykAxNwLArCs16QIDAQABo1MwUTAdBgNVHQ4E
FgQUsuQyYGi78Nt69UmzB1wYo9cA2RIwHwYDVR0jBBgwFoAUsuQyYGi78Nt69Umz
B1wYo9cA2RIwDwYDVR0TAQH/BAUwAwEB/zANBgkqhkiG9w0BAQsFAAOCAQEACqrH
EL6VW2AdsG2iiEESjjyEQ14oY8gs4N6s5kcUunaRVeOh7VFDTZHCZxRdgyoMKWtk
uHg8sbHCML+OJqbBcSNdrTuOlSLfivV8YoWXtIyVVVQjIW7xhLA/APZWgVzI84DV
SfUQncj1gy5ldoQ6vgFHqov4pEzs3rddhZRsTTzb3w/4rABnEr7GHt2cCPkFh+ad
DZZehGUPYrKsU5M4qef96ZRj00Ekkd1Tgtjjc0sJ31CDuQHYz2IW4IP/oe+oQN+q
G433SCIFXArNkaqB2x3nE/PCpGYXKYjseCLF41X8I1YmWiDSABF3Zm5+bbiTD1gf
0Xn2hYS+/vQW2d4Fww==
-----END CERTIFICATE-----"""


def write_static_cert_files():
    base_dir = "/recording" if os.path.isdir("/recording") else tempfile.gettempdir()
    cert_path = os.path.join(base_dir, "server.crt")
    key_path = os.path.join(base_dir, "server.key")

    if not os.path.exists(cert_path):
        with open(cert_path, "wb") as f:
            f.write(CERT_PEM)
    if not os.path.exists(key_path):
        with open(key_path, "wb") as f:
            f.write(KEY_PEM)

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

    print("1. Loading static self-signed certificate...", flush=True)
    cert_path, key_path = write_static_cert_files()

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


if __name__ == "__main__":
    print("=== pyopenssl_test ===", flush=True)
    test_pyopenssl_client_server()
    print("\nAll pyOpenSSL tests passed.", flush=True)
