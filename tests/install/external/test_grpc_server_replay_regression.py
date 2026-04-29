"""Regression: replaying real grpc.server() construction must not segfault."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.helpers import run_record, run_replay


pytest.importorskip("grpc")


def test_replay_grpc_server_construction_does_not_segfault(tmp_path: Path):
    script = tmp_path / "grpc_server_repro.py"
    script.write_text(
        (
            "from concurrent import futures\n"
            "\n"
            "import grpc\n"
            "\n"
            "\n"
            "def main():\n"
            "    print('=== grpc_server_repro ===', flush=True)\n"
            "    server = grpc.server(futures.ThreadPoolExecutor(max_workers=1))\n"
            "    print(type(server).__name__, flush=True)\n"
            "    server.stop(0).wait(timeout=5)\n"
            "    print('ok', flush=True)\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
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
        "record failed for grpc.server construction reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay crashed or diverged for grpc.server construction reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
