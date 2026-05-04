from app.web import create_app


def main():
    app = create_app()
    client = app.test_client()

    response = client.get("/?min_price=3")
    print("INDEX", response.status_code, response.data.count(b"<li"))

    api_response = client.get("/api/products/A100")
    print("API", api_response.status_code, api_response.json["status"])

    missing_response = client.get("/api/products/NOPE")
    print("MISSING", missing_response.status_code, missing_response.json["error"])

    checkout_response = client.post(
        "/checkout",
        data={"sku": "C300", "quantity": "3"},
    )
    print("CHECKOUT", checkout_response.status_code, checkout_response.json["total"])


if __name__ == "__main__":
    main()
