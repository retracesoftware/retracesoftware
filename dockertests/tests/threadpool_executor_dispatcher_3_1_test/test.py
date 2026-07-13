import queue
import threading
from concurrent.futures import ThreadPoolExecutor


def main():
    print("=== threadpool_executor_dispatcher_3_1_test ===", flush=True)

    # Burn executor ids 0, 1, and 2 so the high-contention executor is named
    # ThreadPoolExecutor-3_* like the design-partner trace that exposed this
    # replay divergence.
    for _ in range(3):
        with ThreadPoolExecutor(max_workers=1) as executor:
            assert executor.submit(lambda: "warmup").result() == "warmup"

    semaphore = threading.Semaphore(2)
    results = queue.Queue()

    def worker(index):
        with semaphore:
            results.put(index * 3)

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(worker, index) for index in range(6)]
        for future in futures:
            future.result()

    values = sorted(results.get() for _ in range(6))
    assert values == [0, 3, 6, 9, 12, 15]
    print("threadpool executor dispatcher 3_1 ok", flush=True)


if __name__ == "__main__":
    main()
