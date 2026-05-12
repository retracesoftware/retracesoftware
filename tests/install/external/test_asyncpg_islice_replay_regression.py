"""Regression for asyncpg PidFile replay creating an islice proxy out of order."""

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


_ASYNCPG_ISLICE_PIDFILE_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "asyncpg_islice_pidfile.fixture"
)

_FIXTURE_WORKDIR = Path(
    "/tmp/retrace-asyncpg-fixture-public-path-placeholder-"
    "00000000000000000000000000000000000000000000000000"
)


_ASYNCPG_SCRIPT = """
import asyncio
import os

import asyncpg


CONFIG = {
    "database": os.getenv("DB_NAME", "test_db"),
    "user": os.getenv("DB_USER", "test_user"),
    "password": os.getenv("DB_PASSWORD", "test"),
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "5432")),
}


async def connect():
    last_error = None
    for _ in range(30):
        try:
            return await asyncpg.connect(**CONFIG)
        except Exception as exc:
            last_error = exc
            await asyncio.sleep(0.25)
    raise RuntimeError(f"asyncpg could not connect: {last_error}") from last_error


async def main_async():
    print("=== asyncpg_test ===")
    conn = await connect()
    try:
        await conn.execute("DROP TABLE IF EXISTS retrace_asyncpg_items")
        await conn.execute(
            '''
            CREATE TABLE retrace_asyncpg_items (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                score INTEGER NOT NULL
            )
            '''
        )
        await conn.executemany(
            "INSERT INTO retrace_asyncpg_items(name, score) VALUES($1, $2)",
            [("ada", 3), ("grace", 5), ("katherine", 8)],
        )
        rows = await conn.fetch(
            "SELECT name, score FROM retrace_asyncpg_items ORDER BY id"
        )
        assert [(row["name"], row["score"]) for row in rows] == [
            ("ada", 3),
            ("grace", 5),
            ("katherine", 8),
        ]

        tx = conn.transaction()
        await tx.start()
        await conn.execute(
            "INSERT INTO retrace_asyncpg_items(name, score) VALUES($1, $2)",
            "rollback",
            99,
        )
        await tx.rollback()
        count = await conn.fetchval("SELECT count(*) FROM retrace_asyncpg_items")
        assert count == 3

        await conn.execute("DROP TABLE retrace_asyncpg_items")
        print("asyncpg record/replay scenario ok")
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


def _write_rebased_asyncpg_pidfile_fixture(
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
    replay_env["HOME"] = "/Users/retraceuser00000"
    replay_env.update(
        {
            "DB_HOST": "127.0.0.1",
            "DB_PORT": "15432",
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
    reason=(
        "asyncpg PidFile replay currently creates the internal itertools.islice "
        "proxy where the trace expects the recorded islice result"
    ),
)
def test_asyncpg_captured_pidfile_replay_keeps_islice_result_order() -> None:
    """Regression for Daniel's exact asyncpg replay failure.

    The manual PidFile replay gets into asyncpg's prepared-statement path and
    then consumes the next trace item in the wrong role:

        Checkpoint difference:
        "creating internal proxytype for <class 'itertools.islice'>"
        was expecting {'result': <itertools.islice object ...>}
    """

    pytest.importorskip("asyncpg")

    # The captured trace body contains the script path observed during record.
    # Keep that path stable while rebasing the header around the current Python.
    workdir = _FIXTURE_WORKDIR
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    (workdir / "test.py").write_text(textwrap.dedent(_ASYNCPG_SCRIPT), encoding="utf-8")

    pidfile = workdir / "asyncpg_islice_repro.bin"
    _write_rebased_asyncpg_pidfile_fixture(
        source=_ASYNCPG_ISLICE_PIDFILE_FIXTURE,
        target=pidfile,
        cwd=workdir,
    )

    replay_env = os.environ.copy()
    replay_env.pop("RETRACE_RECORDING", None)
    replay_env.pop("RETRACE_CONFIG", None)
    replay_env.pop("RETRACE_SKIP_CHECKSUMS", None)
    replay_env["PYTHONFAULTHANDLER"] = "1"

    replay = _run(
        [sys.executable, "-m", "retracesoftware", "--recording", str(pidfile)],
        cwd=workdir,
        env=replay_env,
    )
    combined = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        f"asyncpg PidFile replay diverged (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert "creating internal proxytype for <class 'itertools.islice'>" not in combined
    assert "was expecting {'result': <itertools.islice object" not in combined
