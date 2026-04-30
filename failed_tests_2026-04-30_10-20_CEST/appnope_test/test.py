import sys
from multiprocessing import Process

import appnope


def dummy_task():
    return


def test_appnope_with_process():
    """
    This is primarily a macOS library, but the test harness runs in Linux containers.

    - On macOS: call appnope.nope() (disable App Nap) and ensure multiprocessing still works.
    - On non-macOS: appnope should effectively be a no-op; still exercise multiprocessing.
    """
    if sys.platform == "darwin":
        appnope.nope()
        print("disabled app nap", flush=True)
    else:
        print(f"non-macOS platform ({sys.platform}); appnope treated as no-op", flush=True)

    p = Process(target=dummy_task)
    p.start()
    print("process started", flush=True)

    p.terminate()
    p.join(timeout=5)
    print("terminated", flush=True)


if __name__ == "__main__":
    print("=== appnope_test ===", flush=True)
    test_appnope_with_process()
