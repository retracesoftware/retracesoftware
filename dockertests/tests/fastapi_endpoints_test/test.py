from fastapi import FastAPI


def test_fastapi_app_schema():
    app = FastAPI(title="Retrace FastAPI Smoke")
    schema = app.openapi()

    assert schema["info"]["title"] == "Retrace FastAPI Smoke"
    assert app.router is not None
    print("FastAPI app and OpenAPI schema construction work.", flush=True)


if __name__ == "__main__":
    print("=== fastapi_endpoints_test ===", flush=True)
    test_fastapi_app_schema()
