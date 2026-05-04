from flask import Flask, jsonify, request

from app.service import InventoryService


def create_app():
    app = Flask(__name__)
    service = InventoryService()

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    @app.get("/products")
    def products():
        min_price = int(request.args.get("min_price", "0"))
        return jsonify({"products": service.list_products(min_price=min_price)})

    @app.post("/quote")
    def quote():
        payload = request.get_json()
        result = service.quote(payload["sku"], int(payload["quantity"]))
        status = 404 if result.get("error") else 200
        return jsonify(result), status

    return app
