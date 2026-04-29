import asyncio
from datetime import datetime

import httpx
from async_lru import alru_cache


# Mock API endpoint for testing purposes
MOCK_API_URL = "https://jsonplaceholder.typicode.com/posts"


@alru_cache(maxsize=3)  # Cache up to 3 recent requests
async def get_clinician_availability(clinician_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{MOCK_API_URL}/{clinician_id}")
        response.raise_for_status()
        data = response.json()
        return {
            "clinician_id": clinician_id,
            "available": data["id"] % 2 == 0,
            "next_available": datetime.now(),
        }


async def test_async_lru_cache():
    clinician_ids = ["1", "2", "3", "4"]

    for clinician_id in clinician_ids:
        print(f"Fetching availability for clinician {clinician_id}...", flush=True)
        result_1 = await get_clinician_availability(clinician_id)
        print(f"First fetch result: {result_1}", flush=True)

        result_2 = await get_clinician_availability(clinician_id)
        print(f"Second fetch result (should be cached): {result_2}", flush=True)

        if result_1 == result_2:
            print(f"Cache hit for clinician {clinician_id}", flush=True)
        else:
            print(f"Cache miss for clinician {clinician_id}", flush=True)

    extra_clinician_id = "5"
    print(f"Fetching availability for clinician {extra_clinician_id} to test eviction...", flush=True)
    await get_clinician_availability(extra_clinician_id)

    result_3 = await get_clinician_availability("1")
    print(f"Third fetch result (cache may be evicted for '1'): {result_3}", flush=True)


if __name__ == "__main__":
    print("=== asynclru_test ===", flush=True)
    asyncio.run(test_async_lru_cache())
