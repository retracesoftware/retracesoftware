import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import requests


class StreamingHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_GET(self):
        if self.path != "/stream":
            self.send_response(404)
            self.end_headers()
            return

        chunks = [b"alpha\n", b"beta\n", b"gamma\n"]
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(sum(len(chunk) for chunk in chunks)))
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(chunk)
            self.wfile.flush()


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    print("=== http_streaming_requests_test ===", flush=True)
    port = free_port()
    server = HTTPServer(("127.0.0.1", port), StreamingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        response = requests.get(
            f"http://127.0.0.1:{port}/stream",
            stream=True,
            timeout=5,
        )
        response.raise_for_status()
        chunks = [
            chunk.decode("utf-8")
            for chunk in response.iter_content(chunk_size=6)
            if chunk
        ]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert "".join(chunks) == "alpha\nbeta\ngamma\n"
    print(f"chunks={chunks}", flush=True)
    print("http streaming requests ok", flush=True)


if __name__ == "__main__":
    main()
