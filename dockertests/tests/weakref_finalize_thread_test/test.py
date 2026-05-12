import gc
import threading
import weakref


class Payload:
    pass


def main():
    print("=== weakref_finalize_thread_test ===")
    results = []

    def worker():
        payload = Payload()
        weakref.finalize(payload, results.append, "finalized")
        del payload
        gc.collect()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert results
    value = results[0]
    assert value == "finalized"
    print(f"value={value}")
    print("weakref finalize thread ok")


if __name__ == "__main__":
    main()
