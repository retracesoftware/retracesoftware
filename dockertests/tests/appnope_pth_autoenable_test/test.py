import sys
from multiprocessing import Process

import appnope


def dummy_task():
    return None


def test_appnope_with_process():
    """
    Reproduce the pth auto-enable appnope/multiprocessing replay failure.

    On macOS this calls appnope.nope(), starts a multiprocessing child, and
    terminates it.  Recording succeeds with:

        RETRACE_RECORDING=test.retrace python test.py

    The extracted PidFile currently fails during replay bootstrap while
    patching already-loaded _io.open.
    """
    if sys.platform == "darwin":
        appnope.nope()
        print("disabled app nap", flush=True)
    else:
        print(f"non-macOS platform ({sys.platform}); appnope treated as no-op", flush=True)

    proc = Process(target=dummy_task)
    proc.start()
    print("process started", flush=True)

    proc.terminate()
    proc.join(timeout=5)
    print("terminated", flush=True)


if __name__ == "__main__":
    print("=== appnope_pth_autoenable_test ===", flush=True)
    test_appnope_with_process()
