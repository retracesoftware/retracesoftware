import subprocess
import time


def test_popen_terminate_wait_timeout():
    """Exercise subprocess shutdown through terminate() and wait(timeout=...)."""

    print("=== subprocess_terminate_wait_timeout_test ===", flush=True)
    proc = subprocess.Popen(["/bin/sleep", "30"])
    print("pid", proc.pid, flush=True)

    time.sleep(0.05)
    proc.terminate()
    print("wait", proc.wait(timeout=5), flush=True)


if __name__ == "__main__":
    test_popen_terminate_wait_timeout()
