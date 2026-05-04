from flask import Flask, jsonify, render_template, request, session

from app.repository import ProductRepository
from app.service import ProductNotFound, ProductService


def create_app():
    app = Flask(__name__)
    app.secret_key = "deterministic-secret"

    repository = ProductRepository()
    service = ProductService(repository)

    @app.get("/")
    def index():
        min_price = int(request.args.get("min_price", "0"))
        products = service.visible_products(min_price=min_price)
        session["last_count"] = len(products)
        return render_template("index.html", products=products)

    @app.get("/api/products/<sku>")
    def product_api(sku):
        try:
            detail = service.product_detail(sku)
        except ProductNotFound:
            return jsonify({"error": "not-found", "sku": sku}), 404

        return jsonify(detail)

    @app.post("/checkout")
    def checkout():
        sku = request.form["sku"]
        quantity = int(request.form.get("quantity", "1"))

        detail = service.product_detail(sku)
        total = detail["price"] * quantity

        return jsonify(
            {
                "sku": sku,
                "quantity": quantity,
                "total": total,
                "last_count": session.get("last_count"),
            }
        )

    return app
