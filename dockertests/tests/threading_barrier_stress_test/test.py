import queue
import threading


def main():
    print("=== threading_barrier_stress_test ===")
    barrier = threading.Barrier(7)
    results = queue.Queue()

    def worker(index):
        for round_no in range(10):
            barrier.wait()
            results.put((round_no, index, index * index))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()

    for _ in range(10):
        barrier.wait()
    for thread in threads:
        thread.join()

    values = sorted(results.get() for _ in range(60))
    assert len(values) == 60
    assert values[0] == (0, 0, 0)
    assert values[-1] == (9, 5, 25)
    print(f"values={len(values)}")
    print("threading barrier stress ok")


if __name__ == "__main__":
    main()
