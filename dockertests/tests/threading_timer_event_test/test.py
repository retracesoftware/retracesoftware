import threading


def main():
    print("=== threading_timer_event_test ===")
    event = threading.Event()
    results = []
    lock = threading.Lock()

    def fire():
        with lock:
            results.append("timer-fired")
        event.set()

    timer = threading.Timer(0.02, fire)
    timer.start()
    assert event.wait(2)
    timer.join()

    assert results == ["timer-fired"]
    print(f"results={results}")
    print("threading timer/event ok")


if __name__ == "__main__":
    main()
