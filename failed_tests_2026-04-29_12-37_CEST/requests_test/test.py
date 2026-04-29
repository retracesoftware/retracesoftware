import requests


URL = "https://httpbin.org/get?patient_id=p123&status=active"


def fetch_patient_data():
    response = requests.get(URL, timeout=10)
    response.raise_for_status()

    data = response.json()
    print("Response Data:", data, flush=True)

    assert data["args"] == {"patient_id": "p123", "status": "active"}
    assert data["url"] == URL


def test_requests_with_io():
    fetch_patient_data()


if __name__ == "__main__":
    print("=== requests_test ===", flush=True)
    test_requests_with_io()
