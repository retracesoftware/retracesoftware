import requests
import responses


URL = "https://api.example.test/users/7"


@responses.activate
def main():
    print("=== responses_requests_test ===")
    responses.add(
        responses.GET,
        URL,
        json={"id": 7, "name": "Ada"},
        status=200,
        headers={"X-Retrace": "ok"},
    )

    response = requests.get(URL, timeout=5)
    payload = response.json()
    assert response.status_code == 200
    assert payload == {"id": 7, "name": "Ada"}
    print(f"user={payload['id']} name={payload['name']} header={response.headers['X-Retrace']}")
    print("responses/requests ok")


if __name__ == "__main__":
    main()
