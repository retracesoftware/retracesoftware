import asyncio

import aiorwlock


async def reader(lock, id):
    async with lock.reader_lock:
        print(f"[Reader-{id}] Acquired read lock", flush=True)
        await asyncio.sleep(0.5)
        print(f"[Reader-{id}] Released read lock", flush=True)


async def writer(lock, id):
    async with lock.writer_lock:
        print(f"[Writer-{id}] Acquired write lock", flush=True)
        await asyncio.sleep(0.5)
        print(f"[Writer-{id}] Released write lock", flush=True)


async def main():
    lock = aiorwlock.RWLock()

    await asyncio.gather(
        reader(lock, 1),
        reader(lock, 2),
        writer(lock, 1),
        reader(lock, 3),
        writer(lock, 2),
    )


if __name__ == "__main__":
    print("=== aiorwlock_test ===", flush=True)
    asyncio.run(main())
