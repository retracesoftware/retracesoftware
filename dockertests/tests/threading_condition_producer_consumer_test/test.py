import collections
import threading


def main():
    print("=== threading_condition_producer_consumer_test ===")
    condition = threading.Condition()
    pending = collections.deque()
    consumed = []
    done = False

    def producer():
        nonlocal done
        for value in range(5):
            with condition:
                pending.append(value)
                condition.notify()
        with condition:
            done = True
            condition.notify_all()

    def consumer():
        while True:
            with condition:
                while not pending and not done:
                    condition.wait()
                if not pending and done:
                    return
                consumed.append(pending.popleft())

    consumer_thread = threading.Thread(target=consumer)
    producer_thread = threading.Thread(target=producer)
    consumer_thread.start()
    producer_thread.start()
    producer_thread.join()
    consumer_thread.join()

    assert consumed == [0, 1, 2, 3, 4]
    print(f"consumed={consumed}")
    print("threading condition producer/consumer ok")


if __name__ == "__main__":
    main()
