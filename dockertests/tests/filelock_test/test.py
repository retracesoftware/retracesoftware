from filelock import FileLock, Timeout


def test_filelock_configuration():
    lock = FileLock("shared_log.txt.lock", timeout=2)

    assert lock.lock_file == "shared_log.txt.lock"
    assert lock.timeout == 2
    assert issubclass(Timeout, Timeout)
    print("FileLock configuration objects work.", flush=True)


if __name__ == "__main__":
    print("=== filelock_test ===", flush=True)
    test_filelock_configuration()
