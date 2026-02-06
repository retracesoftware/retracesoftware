import subprocess
import sys


def create_large_list(size):
    """Create a large list to test memory allocation."""
    return [i for i in range(size)]


def create_memory_leak():
    """Simulate a potential memory leak by creating objects."""
    data = []
    for i in range(1000):
        data.append([i] * 100)
    return data


def memory_intensive_operation():
    """Perform memory-intensive operations."""
    lists = []
    for _i in range(10):
        lists.append(create_large_list(10000))

    total = sum(len(lst) for lst in lists)
    return total


def test_memray_profiling():
    """
    This test doesn't actually run memray (it's typically a CLI profiler), but it:
    - exercises allocation-heavy code paths
    - checks if memray is installed
    - reports RSS via psutil if available
    """
    print("Testing memray memory profiling...", flush=True)

    print("\n1. Testing basic memory allocation...", flush=True)
    small_list = create_large_list(1000)
    print(f"Created list with {len(small_list)} elements", flush=True)

    medium_list = create_large_list(10000)
    print(f"Created list with {len(medium_list)} elements", flush=True)

    print("\n2. Testing memory-intensive operations...", flush=True)
    result = memory_intensive_operation()
    print(f"Memory-intensive operation completed, total elements: {result}", flush=True)

    print("\n3. Testing potential memory leak simulation...", flush=True)
    leak_data = create_memory_leak()
    print(f"Created potential leak data with {len(leak_data)} items", flush=True)

    print("\n4. Testing memray availability...", flush=True)
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "show", "memray"], capture_output=True, text=True
    )
    if proc.returncode == 0:
        print("memray is available", flush=True)
        print(proc.stdout, end="", flush=True)
    else:
        print("memray not installed (expected for this test)", flush=True)

    print("\n5. Testing memory usage reporting...", flush=True)
    try:
        import psutil

        process = psutil.Process()
        memory_info = process.memory_info()
        print(f"Current memory usage: {memory_info.rss / 1024 / 1024:.2f} MB", flush=True)
    except ImportError:
        print("psutil not available for memory reporting", flush=True)

    print("\nAll memray profiling tests completed!", flush=True)


if __name__ == "__main__":
    print("=== memray_test ===", flush=True)
    test_memray_profiling()
