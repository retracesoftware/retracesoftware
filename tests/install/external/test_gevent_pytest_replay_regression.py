"""Regression for gevent/greenlet WSGI under pytest replay divergence."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.install.external._pytest_replay_regression_helpers import (
    assert_successful_replay,
    record_extract_replay_pytest,
)


@pytest.mark.xfail(
    strict=True,
    reason="gevent/greenlet WSGI under pytest currently exits during replay",
)
def test_gevent_wsgi_pytest_replays_local_request(tmp_path: Path) -> None:
    pytest.importorskip("gevent")
    pytest.importorskip("requests")

    files = {
        "tests/test_gevent_wsgi.py": """
            from gevent import monkey
            monkey.patch_all()

            import json

            import requests
            from gevent import spawn
            from gevent.pywsgi import WSGIServer


            def app(environ, start_response):
                body = json.dumps({"ok": True}).encode()
                start_response(
                    "200 OK",
                    [
                        ("Content-Type", "application/json"),
                        ("Content-Length", str(len(body))),
                    ],
                )
                return [body]


            def test_gevent_wsgi_server():
                server = WSGIServer(("127.0.0.1", 0), app)
                server.start()
                greenlet = spawn(server.serve_forever)
                try:
                    response = requests.get(
                        f"http://127.0.0.1:{server.server_port}/",
                        timeout=5,
                    )
                    assert response.json() == {"ok": True}
                finally:
                    server.stop(timeout=1)
                    greenlet.kill()
        """,
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_gevent_wsgi.py::test_gevent_wsgi_server",
            "-q",
            "--capture=sys",
            "-p",
            "no:cacheprovider",
        ],
        env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        timeout=120,
    )

    assert_successful_replay(record, replay, "1 passed")
