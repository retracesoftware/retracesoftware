import threading


def main():
    print("=== threading_local_test ===")
    local_state = threading.local()
    results = []

    def worker(index):
        local_state.value = f"worker-{index}"
        results.append((index, local_state.value))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    values = sorted(results)
    assert values == [(0, "worker-0"), (1, "worker-1"), (2, "worker-2"), (3, "worker-3")]
    print(f"values={values}")
    print("threading local ok")


if __name__ == "__main__":
    main()
