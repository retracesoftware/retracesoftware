import coreapi


def test_coreapi_get_request():
    # Create a CoreAPI client
    client = coreapi.Client()

    # Make a GET request to a public API (e.g., GitHub's API)
    url = "https://api.github.com"

    response = client.get(url)

    assert "current_user_url" in response, "Expected key 'current_user_url' not found in response."
    print("CoreAPI GET request test passed!", flush=True)


if __name__ == "__main__":
    print("=== coreapi_test ===", flush=True)
    test_coreapi_get_request()
