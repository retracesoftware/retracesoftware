import queue
import threading


def main():
    print("=== threading_semaphore_stress_test ===")
    semaphore = threading.Semaphore(2)
    results = queue.Queue()

    def worker(index):
        with semaphore:
            results.put(index * 3)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    values = sorted(results.get() for _ in threads)
    assert values == [0, 3, 6, 9, 12, 15]
    print(f"values={values}")
    print("threading semaphore stress ok")


if __name__ == "__main__":
    main()
