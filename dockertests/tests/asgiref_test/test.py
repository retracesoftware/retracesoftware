import asyncio

from asgiref.sync import sync_to_async


def sync_function(x, y):
    return x + y


async def test_sync_to_async():
    async_function = sync_to_async(sync_function)
    result = await async_function(5, 3)

    assert result == 8, "Expected the sum to be 8"
    print("Test passed! sync_to_async works correctly.", flush=True)


if __name__ == "__main__":
    print("=== asgiref_test ===", flush=True)
    asyncio.run(test_sync_to_async())
