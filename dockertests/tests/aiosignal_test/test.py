import asyncio

from aiosignal import Signal


class DummyOwner:
    pass


async def main():
    signal = Signal(owner=DummyOwner())

    async def handler():
        print("Handler executed", flush=True)

    signal.append(handler)  # Attach a single handler
    signal.freeze()  # Freeze to allow sending

    await signal.send()  # Trigger the signal


if __name__ == "__main__":
    print("=== aiosignal_test ===", flush=True)
    asyncio.run(main())
