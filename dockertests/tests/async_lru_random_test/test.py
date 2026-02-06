import asyncio
import random
from datetime import datetime, timedelta

from async_lru import alru_cache


@alru_cache(maxsize=3)  # Cache up to 3 recent requests
async def get_clinician_availability(clinician_id: str):
    # Simulate a delay for fetching data, like an API or DB call
    await asyncio.sleep(1)
    # Return a dummy schedule with random availability for the given clinician
    return {
        "clinician_id": clinician_id,
        "available": bool(random.getrandbits(1)),  # Random availability status
        "next_available": datetime.now() + timedelta(days=random.randint(1, 7)),
    }


async def test_async_lru_cache():
    clinician_ids = ["c123", "c456", "c789", "c101"]

    # Fetch availability for each clinician twice to test caching
    for clinician_id in clinician_ids:
        print(f"Fetching availability for clinician {clinician_id}...", flush=True)
        result_1 = await get_clinician_availability(clinician_id)
        print(f"First fetch result: {result_1}", flush=True)

        # Immediate second fetch to test cache retrieval
        result_2 = await get_clinician_availability(clinician_id)
        print(f"Second fetch result (should be cached): {result_2}", flush=True)

        if result_1 == result_2:
            print(f"Cache hit for clinician {clinician_id}", flush=True)
        else:
            print(f"Cache miss for clinician {clinician_id}", flush=True)

    # Fetch one more time with an ID beyond the cache size to test cache eviction
    extra_clinician_id = "c112"
    print(f"Fetching availability for clinician {extra_clinician_id} to test eviction...", flush=True)
    await get_clinician_availability(extra_clinician_id)

    # Check if the first clinician_id ("c123") is still in cache
    result_3 = await get_clinician_availability("c123")
    print(f"Third fetch result (cache may be evicted for 'c123'): {result_3}", flush=True)


if __name__ == "__main__":
    print("=== async_lru_random_test ===", flush=True)
    asyncio.run(test_async_lru_cache())
