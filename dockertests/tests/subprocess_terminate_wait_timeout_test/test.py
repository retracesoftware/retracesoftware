import subprocess
import time


def test_popen_terminate_wait_timeout():
    """Exercise the same shutdown path used by invoice-parser's llama-server."""

    print("=== subprocess_terminate_wait_timeout_test ===", flush=True)
    proc = subprocess.Popen(["/bin/sleep", "30"])
    print("pid", proc.pid, flush=True)

    time.sleep(0.05)
    proc.terminate()
    print("wait", proc.wait(timeout=5), flush=True)


if __name__ == "__main__":
    test_popen_terminate_wait_timeout()
