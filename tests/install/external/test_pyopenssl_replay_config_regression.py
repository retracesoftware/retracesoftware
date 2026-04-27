"""Regression coverage for the pyOpenSSL module config."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.helpers import run_record, run_replay


pytest.importorskip("OpenSSL")


def test_replay_pyopenssl_connection_handshake_uses_recorded_error(tmp_path: Path):
    script = tmp_path / "pyopenssl_handshake_repro.py"
    script.write_text(
        (
            "import socket\n"
            "\n"
            "from OpenSSL import SSL\n"
            "\n"
            "\n"
            "def main():\n"
            "    context = SSL.Context(SSL.TLS_CLIENT_METHOD)\n"
            "    context.set_verify(SSL.VERIFY_NONE, lambda *args: True)\n"
            "    sock = socket.socket()\n"
            "    conn = SSL.Connection(context, sock)\n"
            "    conn.set_connect_state()\n"
            "    try:\n"
            "        conn.do_handshake()\n"
            "    except SSL.Error as exc:\n"
            "        print(type(exc).__name__, flush=True)\n"
            "    conn.close()\n"
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
        "record failed for pyOpenSSL handshake reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )

    replay = run_replay(str(recording), env=env)
    assert replay.returncode == 0, (
        "replay diverged for pyOpenSSL handshake reproducer\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout
