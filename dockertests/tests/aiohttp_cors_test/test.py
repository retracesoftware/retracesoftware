import aiohttp
import aiohttp.web
import aiohttp_cors
import asyncio

# Handler for CORS-enabled endpoint
async def handler(request):
    return aiohttp.web.Response(text="CORS works")

def main():
    app = aiohttp.web.Application()

    # Register route
    resource = app.router.add_resource("/cors")
    route = resource.add_route("GET", handler)

    # Setup CORS with default settings
    cors = aiohttp_cors.setup(app)
    cors.add(route)

    # Run the app in a separate thread using asyncio
    async def run():
        runner = aiohttp.web.AppRunner(app)
        await runner.setup()
        site = aiohttp.web.TCPSite(runner, "127.0.0.1", 8089)
        await site.start()
        print("[SERVER] Running on http://127.0.0.1:8089/cors")

        # Make a client request with Origin header to trigger CORS processing
        async with aiohttp.ClientSession() as session:
            headers = {"Origin": "http://example.com"}
            async with session.get("http://127.0.0.1:8089/cors", headers=headers) as resp:
                text = await resp.text()
                print(f"[CLIENT] Status: {resp.status}, Text: {text}")
                print(f"[CLIENT] CORS Headers: {dict(resp.headers)}")

        await runner.cleanup()

    asyncio.run(run())

if __name__ == "__main__":
    main() 