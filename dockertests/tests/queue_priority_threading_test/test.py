import queue
import threading


def main():
    print("=== queue_priority_threading_test ===")
    work = queue.PriorityQueue()
    results = queue.Queue()

    for priority, name in [(3, "third"), (1, "first"), (2, "second")]:
        work.put((priority, name))
    work.put((99, None))

    def worker():
        while True:
            priority, name = work.get()
            try:
                if name is None:
                    return
                results.put((priority, name.upper()))
            finally:
                work.task_done()

    thread = threading.Thread(target=worker)
    thread.start()
    work.join()
    thread.join()

    values = [results.get() for _ in range(3)]
    assert values == [(1, "FIRST"), (2, "SECOND"), (3, "THIRD")]
    print(f"values={values}")
    print("queue priority threading ok")


if __name__ == "__main__":
    main()
