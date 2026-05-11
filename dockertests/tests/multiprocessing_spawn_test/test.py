import multiprocessing as mp
import os


def worker(input_queue, output_queue):
    values = input_queue.get(timeout=5)
    output_queue.put(
        {
            "pid_is_child": os.getpid() != os.getppid(),
            "total": sum(values),
            "count": len(values),
        }
    )


def main():
    print("=== multiprocessing_spawn_test ===", flush=True)
    context = mp.get_context("spawn")
    input_queue = context.Queue()
    output_queue = context.Queue()
    input_queue.put([2, 3, 5, 7])

    process = context.Process(target=worker, args=(input_queue, output_queue))
    process.start()
    result = output_queue.get(timeout=10)
    process.join(timeout=10)

    assert process.exitcode == 0
    assert result["total"] == 17
    assert result["count"] == 4
    assert result["pid_is_child"] is True
    print(f"result={result}", flush=True)
    print("multiprocessing spawn ok", flush=True)


if __name__ == "__main__":
    main()
