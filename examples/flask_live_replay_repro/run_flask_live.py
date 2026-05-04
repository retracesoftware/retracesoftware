import socket
import threading
import time

import requests
from werkzeug.serving import make_server

from app.web import create_app


class ServerThread(threading.Thread):
    def __init__(self):
        super().__init__(name="flask-live-server")
        self.app = create_app()
        self.server = make_server("127.0.0.1", 0, self.app)
        self.port = self.server.server_port

    def run(self):
        self.server.serve_forever()

    def stop(self):
        self.server.shutdown()


def wait_for_port(port):
    deadline = time.time() + 5
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)

    raise RuntimeError("server did not become ready")


def main():
    server = ServerThread()
    server.start()
    wait_for_port(server.port)

    base_url = f"http://127.0.0.1:{server.port}"

    try:
        health = requests.get(f"{base_url}/health", timeout=5)
        print("HEALTH", health.status_code, health.json()["ok"])

        products = requests.get(f"{base_url}/products?min_price=3", timeout=5)
        product_data = products.json()["products"]
        print("PRODUCTS", products.status_code, len(product_data), product_data[0]["sku"])

        quote = requests.post(
            f"{base_url}/quote",
            json={"sku": "C300", "quantity": 3},
            timeout=5,
        )
        print("QUOTE", quote.status_code, quote.json()["total"])

        missing = requests.post(
            f"{base_url}/quote",
            json={"sku": "NOPE", "quantity": 1},
            timeout=5,
        )
        print("MISSING", missing.status_code, missing.json()["error"])
    finally:
        server.stop()
        server.join(timeout=5)


if __name__ == "__main__":
    main()
