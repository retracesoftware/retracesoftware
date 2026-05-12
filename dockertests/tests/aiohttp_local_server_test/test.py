import asyncio

from aiohttp import ClientSession, web


async def health(request):
    return web.json_response({"ok": True, "path": request.path})


async def run():
    app = web.Application()
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    try:
        async with ClientSession() as session:
            async with session.get(f"http://127.0.0.1:{port}/health") as response:
                payload = await response.json()
                assert response.status == 200
                assert payload == {"ok": True, "path": "/health"}
                print(f"status={response.status} path={payload['path']}")
    finally:
        await runner.cleanup()


def main():
    print("=== aiohttp_local_server_test ===")
    asyncio.run(run())
    print("aiohttp local server ok")


if __name__ == "__main__":
    main()
