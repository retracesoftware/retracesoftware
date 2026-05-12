import threading


def main():
    print("=== threading_basic_join_test ===")
    results = []

    def worker(index):
        results.append((index, index + 10))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    values = sorted(results)
    assert values == [(0, 10), (1, 11), (2, 12), (3, 13)]
    print(f"values={values}")
    print("threading basic join ok")


if __name__ == "__main__":
    main()
