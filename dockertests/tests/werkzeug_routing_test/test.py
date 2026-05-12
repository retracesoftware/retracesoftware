from werkzeug.routing import Map, Rule
from werkzeug.test import EnvironBuilder
from werkzeug.wrappers import Request, Response


def main():
    print("=== werkzeug_routing_test ===")
    routes = Map(
        [
            Rule("/users/<int:user_id>", endpoint="user_detail"),
            Rule("/health", endpoint="health"),
        ]
    )

    adapter = routes.bind("example.test")
    endpoint, values = adapter.match("/users/42")
    assert endpoint == "user_detail"
    assert values == {"user_id": 42}
    print(f"matched {endpoint} {values['user_id']}")

    builder = EnvironBuilder(method="POST", path="/health", data={"ok": "yes"})
    request = Request(builder.get_environ())
    response = Response(f"{request.method}:{request.path}:{request.form['ok']}", status=201)
    print(f"response {response.status_code} {response.get_data(as_text=True)}")
    assert response.get_data(as_text=True) == "POST:/health:yes"
    print("werkzeug routing ok")


if __name__ == "__main__":
    main()
