from pathlib import Path

from platformdirs import PlatformDirs


def main():
    print("=== platformdirs_test ===")
    dirs = PlatformDirs("RetraceDemo", "RetraceSoftware")
    cache = Path(dirs.user_cache_dir)
    data = Path(dirs.user_data_dir)

    assert "RetraceDemo" in str(cache)
    assert "RetraceDemo" in str(data)
    print(f"cache_tail={cache.name}")
    print(f"data_tail={data.name}")
    print("platformdirs ok")


if __name__ == "__main__":
    main()
