import os
import subprocess
import sys
import time


def cpu_intensive_function():
    result = 0
    for i in range(1_000_000):
        result += i * i
    return result


def memory_intensive_function():
    data = []
    for i in range(10_000):
        data.append([i] * 100)
    return len(data)


def io_intensive_function():
    import tempfile

    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        for i in range(1000):
            f.write(f"Line {i}\n")
    os.unlink(f.name)


def test_pyspy_profiling():
    print("Testing py-spy profiling...", flush=True)

    print("\n1. Running CPU-intensive function...", flush=True)
    start_time = time.time()
    result = cpu_intensive_function()
    cpu_time = time.time() - start_time
    print(f"CPU function completed in {cpu_time:.3f}s, result: {result}", flush=True)

    print("\n2. Running memory-intensive function...", flush=True)
    start_time = time.time()
    result = memory_intensive_function()
    memory_time = time.time() - start_time
    print(f"Memory function completed in {memory_time:.3f}s, result: {result}", flush=True)

    print("\n3. Running I/O-intensive function...", flush=True)
    start_time = time.time()
    io_intensive_function()
    io_time = time.time() - start_time
    print(f"I/O function completed in {io_time:.3f}s", flush=True)

    print("\n4. Testing py-spy availability...", flush=True)
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "show", "py-spy"], capture_output=True, text=True
    )
    if proc.returncode == 0:
        print("py-spy is available", flush=True)
        print(proc.stdout, end="", flush=True)
    else:
        print("py-spy not installed (expected for this test)", flush=True)

    print("\nAll py-spy profiling tests completed!", flush=True)


if __name__ == "__main__":
    print("=== pyspy_test ===", flush=True)
    test_pyspy_profiling()
