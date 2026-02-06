import json

import httpcore


URL = "https://httpbin.org/get?patient_id=p123&status=active"


def fetch_patient_data():
    with httpcore.ConnectionPool() as client:
        headers = {"Accept": "application/json"}
        response = client.request("GET", URL, headers=headers)

        if response.status == 200:
            data = json.loads(response.content)
            print("Response Data:", data, flush=True)
        else:
            raise AssertionError(f"Failed to fetch data: Status code {response.status}")


def test_httpcore_with_io():
    fetch_patient_data()


if __name__ == "__main__":
    print("=== httpcore_test ===", flush=True)
    test_httpcore_with_io()
