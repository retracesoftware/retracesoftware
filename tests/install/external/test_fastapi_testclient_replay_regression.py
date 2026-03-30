"""FastAPI TestClient replay ladder for async vs sync endpoints.

Root component focus:
- FastAPI's execution path above Starlette TestClient and bare AnyIO portal
- whether replay divergence is specific to sync endpoint offloading/dispatch

Ownership signal:
- if async passes and sync fails, the live bug is above Starlette and below
  end-to-end FastAPI application logic
- this is the next rung after the Starlette control in the web replay ladder
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
            "async def homepage():\n"
            "    print('FastAPI async dict endpoint was called', flush=True)\n"
            "    return {'message': 'Hello, FastAPI'}\n",
        ),
        (
            "sync",
            "def homepage():\n"
            "    print('FastAPI sync dict endpoint was called', flush=True)\n"
            "    return {'message': 'Hello, FastAPI'}\n",
        ),
    ],
)
def test_record_then_replay_fastapi_testclient_request_matrix(
    tmp_path: Path, endpoint_kind: str, endpoint_source: str
):
    pytest.importorskip("fastapi")

    script_file = tmp_path / f"fastapi_testclient_{endpoint_kind}.py"
    script_file.write_text(
        (
            "from fastapi import FastAPI\n"
            "from fastapi.testclient import TestClient\n"
            "\n"
            "app = FastAPI()\n"
            "\n"
            "@app.get('/')\n"
            f"{endpoint_source}\n"
            "\n"
            "if __name__ == '__main__':\n"
            f"    print('=== fastapi_{endpoint_kind}_dict ===', flush=True)\n"
            "    client = TestClient(app)\n"
            "    print('Testing root endpoint...', flush=True)\n"
            "    response = client.get('/')\n"
            "    print(f'Response status: {response.status_code}', flush=True)\n"
            "    print(f'Response body: {response.json()}', flush=True)\n"
        ),
        encoding="utf-8",
    )

    trace_file = str(tmp_path / f"fastapi_{endpoint_kind}.retrace")

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"

    record = run_record(str(script_file), trace_file, env=env)
    assert record.returncode == 0, (
        f"record failed for FastAPI TestClient ({endpoint_kind})\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(trace_file, env=env)
    assert replay.returncode == 0, (
        f"replay diverged for FastAPI TestClient ({endpoint_kind})\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
