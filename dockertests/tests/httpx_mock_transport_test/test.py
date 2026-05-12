import httpx


def handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path == "/users/9"
    return httpx.Response(200, json={"id": 9, "name": "Grace"})


def main():
    print("=== httpx_mock_transport_test ===")
    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport, base_url="https://api.example.test") as client:
        response = client.get("/users/9")

    payload = response.json()
    assert payload == {"id": 9, "name": "Grace"}
    print(f"user={payload['id']} name={payload['name']}")
    print("httpx mock transport ok")


if __name__ == "__main__":
    main()
