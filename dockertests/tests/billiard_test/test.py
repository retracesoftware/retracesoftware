import time

from billiard import Process


def worker_task(name):
    print(f"Process {name} starting.", flush=True)
    time.sleep(0.1)
    print(f"Process {name} finished.", flush=True)


def test_parallel_processing():
    num_processes = 2
    processes = []

    for i in range(num_processes):
        process_name = f"Worker-{i+1}"
        process = Process(target=worker_task, args=(process_name,))
        process.start()
        processes.append(process)
        print(f"{process_name} started.", flush=True)

    for process in processes:
        process.join(timeout=5)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        print(f"{process.name} has completed with exitcode {process.exitcode}.", flush=True)


if __name__ == "__main__":
    print("=== billiard_test ===", flush=True)
    test_parallel_processing()
