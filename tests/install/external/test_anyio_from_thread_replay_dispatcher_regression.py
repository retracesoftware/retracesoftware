"""Control: bare anyio blocking portal replay stays green.

Root component focus:
- replay-side thread routing for work scheduled through ``anyio.from_thread``
- native ``utils.Dispatcher`` coordination once a portal worker thread is live

Ownership signal:
- this is the smallest portal setup below Starlette/FastAPI TestClient
- if this starts failing, the remaining web replay issue has moved below the
  framework layer into bare ``anyio.from_thread`` plumbing
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.helpers import run_record, run_replay


def test_replay_anyio_blocking_portal_does_not_diverge(tmp_path: Path):
    pytest.importorskip("anyio")

    script = tmp_path / "anyio_portal_repro.py"
    script.write_text(
        (
            "import anyio\n"
            "from anyio.from_thread import start_blocking_portal\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    print('=== anyio_portal ===', flush=True)\n"
            "    with start_blocking_portal() as portal:\n"
            "        print('portal started', flush=True)\n"
            "        event = portal.call(anyio.Event)\n"
            "        print(f'event_type={type(event).__name__}', flush=True)\n"
            "        print('ok', flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(recording)

    record = run_record(str(script), str(recording), env=env)
    assert record.returncode == 0, (
        "record failed for anyio blocking portal reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay diverged for anyio blocking portal reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
