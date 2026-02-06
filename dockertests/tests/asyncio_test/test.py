import asyncio


# Define an asynchronous function that simulates an I/O operation
async def async_task(delay: int, message: str):
    await asyncio.sleep(delay)  # Simulate a network request or I/O-bound task
    return message


# Test to check the result of the async task
async def test_async_task():
    result = await async_task(1, "Hello, asyncio!")
    assert result == "Hello, asyncio!", f"Expected 'Hello, asyncio!' but got '{result}'"
    print("Test passed!")


# Running the test using asyncio.run()
if __name__ == "__main__":
    asyncio.run(test_async_task())

