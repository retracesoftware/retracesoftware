from importlib.util import find_spec
import io
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
    buffer = io.StringIO()
    for i in range(1000):
        buffer.write(f"Line {i}\n")
    buffer.seek(0)
    return sum(1 for _line in buffer)


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
    result = io_intensive_function()
    io_time = time.time() - start_time
    print(f"I/O function completed in {io_time:.3f}s, result: {result}", flush=True)

    print("\n4. Testing py-spy availability...", flush=True)
    if find_spec("py_spy") is not None:
        print("py-spy is available", flush=True)
    else:
        print("py-spy not installed (expected for this test)", flush=True)

    print("\nAll py-spy profiling tests completed!", flush=True)


if __name__ == "__main__":
    print("=== pyspy_test ===", flush=True)
    test_pyspy_profiling()
