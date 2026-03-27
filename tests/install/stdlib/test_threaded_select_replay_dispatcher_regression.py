"""Regression: replay thread dispatch diverges on child-thread select().

Root component focus:
- replay-side per-thread routing in `stream.reader.DemuxReader`
- native `utils.Dispatcher` that hands recorded events to replay threads

Ownership signal:
- the FastAPI TestClient replay failure reduces to a stdlib child thread that
  performs `select.select()`
- keeping only `select.select` proxied is still sufficient to reproduce the
  same `Dispatcher: too many threads waiting for item` replay failure
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(
    not hasattr(socket, "socketpair"),
    reason="socketpair is required for this regression test",
)


def test_replay_child_thread_select_does_not_diverge(tmp_path: Path):
    script = tmp_path / "thread_select_repro.py"
    script.write_text(
        (
            "import select\n"
            "import socket\n"
            "import threading\n"
            "\n"
            "box = {}\n"
            "\n"
            "def worker():\n"
            "    try:\n"
            "        left, right = socket.socketpair()\n"
            "        right.send(b'x')\n"
            "        readable, _, _ = select.select([left], [], [], 0.1)\n"
            "        box['count'] = len(readable)\n"
            "        left.close()\n"
            "        right.close()\n"
            "    except BaseException as exc:\n"
            "        box['error'] = f'{type(exc).__name__}: {exc}'\n"
            "\n"
            "def main():\n"
            "    thread = threading.Thread(target=worker)\n"
            "    thread.start()\n"
            "    thread.join()\n"
            "    assert box.get('error') is None, box['error']\n"
            "    assert box.get('count') == 1, box\n"
            "    print('ok', flush=True)\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        ),
        encoding="utf-8",
    )

    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    (modules_dir / "select.toml").write_text(
        'proxy = ["select"]\n',
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    extracted = tmp_path / "trace.d"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_RECORDING"] = str(recording)
    env["RETRACE_MODULES_PATH"] = str(modules_dir)

    record = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert record.returncode == 0, (
        "record failed for child-thread select reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    extract = subprocess.run(
        [str(recording), "--extract"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
        env=env,
    )
    assert extract.returncode == 0, (
        "extract failed for child-thread select reproducer\n"
        f"exit: {extract.returncode}\n"
        f"stdout:\n{extract.stdout}\n"
        f"stderr:\n{extract.stderr}"
    )

    index = json.loads((extracted / "index.json").read_text(encoding="utf-8"))
    bin_path = extracted / f"{index['root']['pid']}.bin"

    replay = subprocess.run(
        [str(bin_path)],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=extracted,
        env=env,
    )
    assert replay.returncode == 0, (
        "replay diverged for child-thread select reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
