from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient


async def homepage(request):
    return JSONResponse({"message": "hello", "path": request.url.path})


def main():
    print("=== starlette_testclient_test ===")
    app = Starlette(routes=[Route("/", homepage)])
    with TestClient(app) as client:
        response = client.get("/")

    payload = response.json()
    assert payload == {"message": "hello", "path": "/"}
    print(f"status={response.status_code} message={payload['message']}")
    print("starlette testclient ok")


if __name__ == "__main__":
    main()
