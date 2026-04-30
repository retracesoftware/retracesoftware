"""Regression: HTTPX async DNS can diverge ThreadPoolExecutor worker count.

The async-lru dockertests fail underneath the cache layer: enough sequential
HTTPX async requests can make record start an extra idle default-executor
worker, while replay's faster recorded external calls may not. The branch that
decides whether to start that worker is ``threading.Semaphore.acquire`` inside
``ThreadPoolExecutor._adjust_thread_count``.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.helpers import run_record, run_replay


def test_httpx_async_replay_preserves_executor_worker_count(tmp_path: Path):
    pytest.importorskip("httpx")

    script = tmp_path / "httpx_async_executor_repro.py"
    script.write_text(
        (
            "import asyncio\n"
            "import httpx\n"
            "\n"
            "async def fetch(client, i):\n"
            "    response = await client.get(\n"
            "        f'https://jsonplaceholder.typicode.com/posts/{i}'\n"
            "    )\n"
            "    response.raise_for_status()\n"
            "    print('fetch', response.json()['id'], flush=True)\n"
            "\n"
            "async def main():\n"
            "    async with httpx.AsyncClient() as client:\n"
            "        for i in [1, 2, 3, 4, 5, 1]:\n"
            "            await fetch(client, i)\n"
            "\n"
            "print('=== httpx_async_executor ===', flush=True)\n"
            "asyncio.run(main())\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(recording)

    record = run_record(str(script), str(recording), env=env, stacktraces=False)
    assert record.returncode == 0, (
        "record failed for HTTPX async executor reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), extra_args=["--read_timeout", "1000"], env=env)
    assert replay.returncode == 0, (
        "replay diverged for HTTPX async executor reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
