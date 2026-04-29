import requests
from cachecontrol import CacheControl
from cachecontrol.cache import DictCache


def test_cachecontrol():
    cache = DictCache()
    cache.set("demo-key", b"demo-value")
    assert cache.get("demo-key") == b"demo-value"

    session = CacheControl(requests.Session(), cache=cache)
    adapter = session.get_adapter("https://example.test/")
    assert adapter.__class__.__name__ == "CacheControlAdapter"
    print("CacheControl session mounted with DictCache.", flush=True)


if __name__ == "__main__":
    print("=== cachecontrol_test ===", flush=True)
    test_cachecontrol()
