"""Control: Starlette TestClient replay stays green.

Root component focus:
- replay behavior through ``starlette.testclient.TestClient``
- portal/thread coordination above pure ``anyio.from_thread`` setup

Ownership signal:
- pure AnyIO portal setup can pass while FastAPI replay still fails
- if this starts failing, the remaining issue has moved down from FastAPI into
  Starlette's TestClient layer
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.helpers import run_record, run_replay


@pytest.mark.parametrize(
    ("endpoint_kind", "endpoint_source"),
    [
        (
            "async",
            "async def homepage(request):\n"
            "    print('Starlette async endpoint was called', flush=True)\n"
            "    return JSONResponse({'message': 'Hello, Starlette'})\n",
        ),
        (
            "sync",
            "def homepage(request):\n"
            "    print('Starlette sync endpoint was called', flush=True)\n"
            "    return JSONResponse({'message': 'Hello, Starlette'})\n",
        ),
    ],
)
def test_record_then_replay_starlette_testclient_request(
    tmp_path: Path, endpoint_kind: str, endpoint_source: str
):
    pytest.importorskip("starlette")

    script_file = tmp_path / f"starlette_testclient_{endpoint_kind}.py"
    script_file.write_text(
        (
            "from starlette.applications import Starlette\n"
            "from starlette.responses import JSONResponse\n"
            "from starlette.routing import Route\n"
            "from starlette.testclient import TestClient\n"
            "\n"
            f"{endpoint_source}\n"
            "app = Starlette(routes=[Route('/', homepage)])\n"
            "\n"
            "if __name__ == '__main__':\n"
            f"    print('=== starlette_{endpoint_kind}_test ===', flush=True)\n"
            "    client = TestClient(app)\n"
            "    print('Testing root endpoint...', flush=True)\n"
            "    response = client.get('/')\n"
            "    print(f'Response status: {response.status_code}', flush=True)\n"
            "    print(f'Response body: {response.json()}', flush=True)\n"
        ),
        encoding="utf-8",
    )

    trace_file = str(tmp_path / f"starlette_{endpoint_kind}.retrace")

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"

    record = run_record(str(script_file), trace_file, env=env)
    assert record.returncode == 0, (
        f"record failed for Starlette TestClient ({endpoint_kind})\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(trace_file, env=env)
    assert replay.returncode == 0, (
        f"replay diverged for Starlette TestClient ({endpoint_kind})\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
