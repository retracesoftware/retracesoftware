import random
import time

from billiard import Process, Queue


def worker_task(name, queue):
    print(f"Process {name} starting.", flush=True)
    delay = random.uniform(0.5, 2.0)
    time.sleep(delay)
    result = f"Result from {name} after {delay:.2f} seconds"
    queue.put(result)
    print(f"Process {name} finished.", flush=True)


def test_parallel_processing():
    num_processes = 4
    queue = Queue()
    processes = []

    for i in range(num_processes):
        process_name = f"Worker-{i+1}"
        process = Process(target=worker_task, args=(process_name, queue))
        process.start()
        processes.append(process)
        print(f"{process_name} started.", flush=True)

    for _process in processes:
        result = queue.get()
        print("Received:", result, flush=True)

    for process in processes:
        process.join()
        print(f"{process.name} has completed.", flush=True)


if __name__ == "__main__":
    print("=== billiard_test ===", flush=True)
    test_parallel_processing()
