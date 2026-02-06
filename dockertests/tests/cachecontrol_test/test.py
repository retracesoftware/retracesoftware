import os
import shutil

import requests
from cachecontrol import CacheControl
from cachecontrol.caches.file_cache import FileCache


def test_cachecontrol():
    # Clear the cache directory at the beginning of the test
    cache_dir = ".web_cache"
    if os.path.exists(cache_dir):
        shutil.rmtree(cache_dir)

    # Set up a CacheControl session with file-based cache
    session = CacheControl(requests.Session(), cache=FileCache(cache_dir))

    url = "https://jsonplaceholder.typicode.com/posts/1"  # Mock API endpoint

    # First request (should fetch data from the server and store it in the cache)
    print("Fetching data from server...", flush=True)
    response = session.get(url)
    assert getattr(response, "from_cache", False) is False, "First request should not be from cache"
    print("Response (not cached):", response.json(), flush=True)

    # Second request (should fetch data from the cache)
    print("Fetching data from cache...", flush=True)
    cached_response = session.get(url)
    assert getattr(cached_response, "from_cache", False) is True, "Second request should be from cache"
    print("Response (cached):", cached_response.json(), flush=True)


if __name__ == "__main__":
    print("=== cachecontrol_test ===", flush=True)
    test_cachecontrol()
