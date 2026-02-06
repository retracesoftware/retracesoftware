import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


SERVER_PORT = int(os.environ.get("SERVER_PORT", "5000"))
HEALTH_PATH = os.environ.get("SERVER_HEALTH_PATH", "/health")
PING_PATH = os.environ.get("PING_PATH", "/ping")
RESPONSE_BODY = os.environ.get("RESPONSE_BODY", "ok").encode("utf-8")
RESPONSE_DELAY_MS = float(os.environ.get("RESPONSE_DELAY_MS", "10"))


class PerfHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == HEALTH_PATH:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return

        if self.path.startswith(PING_PATH):
            if RESPONSE_DELAY_MS > 0:
                time.sleep(RESPONSE_DELAY_MS / 1000.0)
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(RESPONSE_BODY)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, _format, *_args):
        return


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", SERVER_PORT), PerfHandler)
    print(f"[server] listening on 0.0.0.0:{SERVER_PORT}", flush=True)
    print(f"[server] response_delay_ms={RESPONSE_DELAY_MS}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
