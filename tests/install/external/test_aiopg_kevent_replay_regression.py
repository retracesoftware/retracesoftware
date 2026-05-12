"""Regression for aiopg PidFile replay desyncing around psycopg2/kqueue."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest
from retracesoftware.tape import checksums


_AIOPG_KEVENT_PIDFILE_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "aiopg_kevent_pidfile.fixture"
)

_FIXTURE_WORKDIR = Path(
    "/tmp/retrace-aiopg-fixture-public-path-placeholder-"
    "00000000000000000000000000000000000000000000000000"
)


_AIOPG_SCRIPT = """
import asyncio
import os

import aiopg


DSN = (
    f"dbname={os.getenv('DB_NAME', 'test_db')} "
    f"user={os.getenv('DB_USER', 'test_user')} "
    f"password={os.getenv('DB_PASSWORD', 'test')} "
    f"host={os.getenv('DB_HOST', 'localhost')} "
    f"port={os.getenv('DB_PORT', '5432')}"
)


async def connect():
    last_error = None
    for _ in range(30):
        try:
            return await aiopg.connect(DSN)
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.25)
    raise RuntimeError(f"aiopg could not connect: {last_error}") from last_error


async def main_async():
    print("=== aiopg_test ===")
    conn = await connect()
    try:
        async with conn.cursor() as cursor:
            await cursor.execute("DROP TABLE IF EXISTS retrace_aiopg_items")
            await cursor.execute(
                '''
                CREATE TABLE retrace_aiopg_items (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    score INTEGER NOT NULL
                )
                '''
            )
            await cursor.execute(
                '''
                INSERT INTO retrace_aiopg_items(name, score)
                VALUES (%s, %s), (%s, %s), (%s, %s)
                ''',
                ("ada", 3, "grace", 5, "katherine", 8),
            )

            await cursor.execute(
                "SELECT name, score FROM retrace_aiopg_items ORDER BY id"
            )
            rows = await cursor.fetchall()
            assert rows == [("ada", 3), ("grace", 5), ("katherine", 8)]

            await cursor.execute("DROP TABLE retrace_aiopg_items")
            print("aiopg record/replay scenario ok")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main_async())
"""


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 45,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _write_rebased_aiopg_pidfile_fixture(
    *,
    source: Path,
    target: Path,
    cwd: Path,
) -> None:
    with source.open("rb") as src:
        shebang = src.readline()
        header = json.loads(src.readline())
        body = src.read()

    replay_env = os.environ.copy()
    replay_env.pop("RETRACE_RECORDING", None)
    replay_env.pop("RETRACE_CONFIG", None)
    replay_env.pop("RETRACE_SKIP_CHECKSUMS", None)
    replay_env["PYTHONFAULTHANDLER"] = "1"
    replay_env["RETRACE_CONFIG"] = "debug"
    replay_env["HOME"] = "/Users/retraceuser00000"
    replay_env.update(
        {
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "5432",
            "DB_NAME": "test_db",
            "DB_USER": "test_user",
            "DB_PASSWORD": "test",
        }
    )

    header["cwd"] = str(cwd)
    header["executable"] = sys.executable
    header["python_version"] = sys.version
    header["checksums"] = checksums()
    header["env"] = replay_env
    header["sys_path"] = [str(cwd)] + [path for path in sys.path if path]

    with target.open("wb") as dst:
        dst.write(shebang)
        dst.write(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        dst.write(b"\n")
        dst.write(body)
    target.chmod(0o755)


@pytest.mark.xfail(
    strict=True,
    reason="aiopg replay currently desyncs psycopg2 connection materialization and kqueue events",
)
def test_aiopg_captured_pidfile_replay_keeps_psycopg2_connection_order() -> None:
    """Regression for the current aiopg replay failure.

    The captured replay currently fails with:

        Checkpoint difference:
        {'function': wrapped_function:kevent.__init__ ...}
        was expecting {'result': <psycopg2.extensions.connection ...>}
    """

    pytest.importorskip("aiopg")

    # The captured trace body contains the script path observed during record.
    # Keep that path stable while rebasing the header around the current Python.
    workdir = _FIXTURE_WORKDIR
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    (workdir / "test.py").write_text(textwrap.dedent(_AIOPG_SCRIPT), encoding="utf-8")

    pidfile = workdir / "aiopg_kevent_repro.bin"
    _write_rebased_aiopg_pidfile_fixture(
        source=_AIOPG_KEVENT_PIDFILE_FIXTURE,
        target=pidfile,
        cwd=workdir,
    )

    replay_env = os.environ.copy()
    replay_env.pop("RETRACE_RECORDING", None)
    replay_env.pop("RETRACE_SKIP_CHECKSUMS", None)
    replay_env["PYTHONFAULTHANDLER"] = "1"
    replay_env["RETRACE_CONFIG"] = "debug"

    replay = _run(
        [sys.executable, "-m", "retracesoftware", "--recording", str(pidfile)],
        cwd=workdir,
        env=replay_env,
    )
    combined = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        f"aiopg PidFile replay diverged (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert "wrapped_function:kevent.__init__" not in combined
    assert "was expecting {'result': <psycopg2.extensions.connection" not in combined
