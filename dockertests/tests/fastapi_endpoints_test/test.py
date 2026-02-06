from fastapi import FastAPI
from fastapi.testclient import TestClient


app = FastAPI()


@app.get("/")
def read_root():
    print("Root endpoint was called", flush=True)
    return {"message": "Hello, FastAPI"}


@app.get("/items/{item_id}")
def read_item(item_id: int, q: str = None):
    print(f"Item endpoint was called with item_id: {item_id} and query: {q}", flush=True)
    return {"item_id": item_id, "q": q}


def test_fastapi_endpoints():
    client = TestClient(app)

    print("Testing root endpoint...", flush=True)
    response = client.get("/")
    print(f"Response status: {response.status_code}", flush=True)
    print(f"Response body: {response.json()}", flush=True)
    assert response.status_code == 200
    assert response.json() == {"message": "Hello, FastAPI"}

    print("\nTesting items endpoint...", flush=True)
    response = client.get("/items/42")
    print(f"Response status: {response.status_code}", flush=True)
    print(f"Response body: {response.json()}", flush=True)
    assert response.status_code == 200
    assert response.json() == {"item_id": 42, "q": None}

    print("\nTesting items endpoint with query parameter...", flush=True)
    response = client.get("/items/123?q=test")
    print(f"Response status: {response.status_code}", flush=True)
    print(f"Response body: {response.json()}", flush=True)
    assert response.status_code == 200
    assert response.json() == {"item_id": 123, "q": "test"}

    print("All FastAPI endpoints tested successfully!", flush=True)


if __name__ == "__main__":
    print("=== fastapi_endpoints_test ===", flush=True)
    test_fastapi_endpoints()
