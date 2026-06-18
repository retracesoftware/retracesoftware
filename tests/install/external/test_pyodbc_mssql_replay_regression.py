"""Regression for pyodbc replay requiring a live SQL Server."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import textwrap

import pytest

from tests.helpers import PYTHON, _run_for_pidfile, local_pythonpath, tail


TIMEOUT = 45


def _clean_env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["MESONPY_EDITABLE_SKIP"] = os.environ.get("MESONPY_EDITABLE_SKIP", "1")
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONPATH"] = os.pathsep.join([str(tmp_path), local_pythonpath()])
    for key in (
        "RETRACE_CONFIG",
        "RETRACE_INODE",
        "RETRACE_RECORDING",
        "RETRACE_SKIP_CHECKSUMS",
    ):
        env.pop(key, None)
    if extra:
        env.update(extra)
    return env


def _mssql_env_from_outer() -> dict[str, str]:
    required = {
        "PYODBC_MSSQL_HOST": "DB_HOST",
        "PYODBC_MSSQL_PORT": "DB_PORT",
        "PYODBC_MSSQL_USER": "DB_USER",
        "PYODBC_MSSQL_PASSWORD": "DB_PASSWORD",
    }
    missing = [outer for outer in required if not os.environ.get(outer)]
    if missing:
        pytest.skip(
            "set PYODBC_MSSQL_HOST/PORT/USER/PASSWORD to run the live pyodbc "
            f"replay regression; missing: {', '.join(missing)}"
        )
    env = {inner: os.environ[outer] for outer, inner in required.items()}
    env["DB_NAME"] = os.environ.get("PYODBC_MSSQL_DATABASE", "master")
    env["DB_DRIVER"] = os.environ.get(
        "PYODBC_MSSQL_DRIVER", "ODBC Driver 18 for SQL Server"
    )
    return env


def _assert_pyodbc_server_available(tmp_path: Path, env: dict[str, str]) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        textwrap.dedent(
            """
            import os
            import pyodbc

            connection = pyodbc.connect(
                "DRIVER={%s};SERVER=%s,%s;DATABASE=%s;UID=%s;PWD=%s;"
                "Encrypt=no;TrustServerCertificate=yes"
                % (
                    os.environ["DB_DRIVER"],
                    os.environ["DB_HOST"],
                    os.environ["DB_PORT"],
                    os.environ["DB_NAME"],
                    os.environ["DB_USER"],
                    os.environ["DB_PASSWORD"],
                ),
                timeout=3,
            )
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT CAST(1 AS INT)")
                assert cursor.fetchone()[0] == 1
            finally:
                connection.close()
            """
        ).lstrip(),
        encoding="utf-8",
    )
    probe_result = subprocess.run(
        [PYTHON, str(probe)],
        cwd=tmp_path,
        env=_clean_env(tmp_path, env),
        text=True,
        capture_output=True,
        timeout=TIMEOUT,
    )
    if probe_result.returncode != 0:
        pytest.skip(
            "configured SQL Server/pyodbc probe is not available\n"
            f"stdout:\n{tail(probe_result.stdout)}\n"
            f"stderr:\n{tail(probe_result.stderr)}"
        )


def test_pyodbc_mssql_replay_does_not_require_live_database(tmp_path: Path) -> None:
    pytest.importorskip("pyodbc")

    record_env = _mssql_env_from_outer()
    _assert_pyodbc_server_available(tmp_path, record_env)

    script = tmp_path / "pyodbc_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            import os
            import pyodbc

            connection = pyodbc.connect(
                "DRIVER={%s};SERVER=%s,%s;DATABASE=%s;UID=%s;PWD=%s;"
                "Encrypt=no;TrustServerCertificate=yes"
                % (
                    os.environ["DB_DRIVER"],
                    os.environ["DB_HOST"],
                    os.environ["DB_PORT"],
                    os.environ["DB_NAME"],
                    os.environ["DB_USER"],
                    os.environ["DB_PASSWORD"],
                ),
                timeout=3,
            )
            try:
                cursor = connection.cursor()
                cursor.execute("SELECT CAST(123 AS INT) AS value")
                print("PYODBC_ROW", cursor.fetchone()[0], flush=True)
            finally:
                connection.close()
            """
        ).lstrip(),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    record = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--",
            str(script),
        ],
        cwd=tmp_path,
        env=_clean_env(tmp_path, record_env),
        timeout=TIMEOUT,
    )
    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\nstderr:\n{tail(record.stderr)}"
    )
    assert "PYODBC_ROW 123" in record.stdout

    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        timeout=TIMEOUT,
    )
    assert extract.returncode == 0, (
        f"extract failed\nstdout:\n{tail(extract.stdout)}\nstderr:\n{tail(extract.stderr)}"
    )

    list_pids = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=tmp_path,
        env=_clean_env(tmp_path),
        timeout=TIMEOUT,
    )
    assert list_pids.returncode == 0
    root_pid = list_pids.stdout.splitlines()[0]

    replay_env = dict(record_env)
    replay_env["DB_HOST"] = "retrace-pyodbc-replay-should-not-connect.invalid"
    replay = _run_for_pidfile(
        [str(tmp_path / "trace.d" / f"{root_pid}.bin")],
        cwd=tmp_path,
        env=_clean_env(tmp_path, replay_env),
        timeout=TIMEOUT,
    )
    combined = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        "pyodbc replay still tried to use the live database instead of the "
        "recorded boundary\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}\n"
        f"replay stdout:\n{tail(replay.stdout)}\n"
        f"replay stderr:\n{tail(replay.stderr)}"
    )
    assert "PYODBC_ROW 123" in combined
    assert "OperationalError" not in combined
    assert "Login timeout expired" not in combined
