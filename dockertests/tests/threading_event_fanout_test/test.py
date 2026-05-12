import threading


def main():
    print("=== threading_event_fanout_test ===")
    ready = threading.Event()
    results = []

    def waiter(index):
        ready.wait()
        results.append(index)

    threads = [threading.Thread(target=waiter, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()

    ready.set()
    for thread in threads:
        thread.join()

    values = sorted(results)
    assert values == list(range(8))
    print(f"values={values}")
    print("threading event fanout ok")


if __name__ == "__main__":
    main()
