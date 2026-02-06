import random

import anyio


async def task(name, delay):
    print(f"Task {name} started with a delay of {delay:.2f} seconds.", flush=True)
    await anyio.sleep(delay)
    print(f"Task {name} completed.", flush=True)


async def main():
    print("Starting concurrent tasks...", flush=True)

    # Schedule multiple tasks with different delays to simulate asynchronous work
    async with anyio.create_task_group() as task_group:
        task_group.start_soon(task, "A", random.uniform(1, 3))
        task_group.start_soon(task, "B", random.uniform(1, 3))
        task_group.start_soon(task, "C", random.uniform(1, 3))

    print("All tasks completed.", flush=True)


if __name__ == "__main__":
    print("=== anyio_test ===", flush=True)
    anyio.run(main)
