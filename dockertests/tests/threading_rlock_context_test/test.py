import threading


def main():
    print("=== threading_rlock_context_test ===")
    lock = threading.RLock()
    values = []

    def add_pair(index):
        with lock:
            values.append(index)
            with lock:
                values.append(index + 10)

    threads = [threading.Thread(target=add_pair, args=(index,)) for index in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(values) == [0, 1, 2, 10, 11, 12]
    print(f"values={sorted(values)}")
    print("threading rlock ok")


if __name__ == "__main__":
    main()
