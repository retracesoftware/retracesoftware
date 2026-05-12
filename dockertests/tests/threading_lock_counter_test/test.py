import threading


def main():
    print("=== threading_lock_counter_test ===")
    lock = threading.Lock()
    counts = {"value": 0}

    def worker(iterations):
        for _ in range(iterations):
            with lock:
                counts["value"] += 1

    threads = [threading.Thread(target=worker, args=(25,)) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert counts["value"] == 100
    print(f"count={counts['value']}")
    print("threading lock counter ok")


if __name__ == "__main__":
    main()
