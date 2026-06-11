"""Regression coverage for in-process moto under Retrace."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import PYTHON, _run_for_pidfile, tail


TIMEOUT = 30


def _run_python(tmp_path: Path, script_name: str, script_source: str):
    script = tmp_path / script_name
    script.write_text(script_source, encoding="utf-8")
    return _run_for_pidfile(
        [PYTHON, str(script)],
        cwd=tmp_path,
        env=None,
        timeout=TIMEOUT,
    )


def _record_script(tmp_path: Path, script_name: str, script_source: str):
    script = tmp_path / script_name
    script.write_text(script_source, encoding="utf-8")
    recording = tmp_path / "trace.retrace"
    record = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--",
            str(script),
        ],
        cwd=tmp_path,
        env=None,
        timeout=TIMEOUT,
    )
    return recording, record


def _extract_and_replay(tmp_path: Path, recording: Path):
    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=None,
        timeout=TIMEOUT,
    )
    assert extract.returncode == 0, (
        f"extract failed\nstdout:\n{tail(extract.stdout)}\n"
        f"stderr:\n{tail(extract.stderr)}"
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
        env=None,
        timeout=TIMEOUT,
    )
    assert list_pids.returncode == 0, (
        f"list_pids failed\nstdout:\n{tail(list_pids.stdout)}\n"
        f"stderr:\n{tail(list_pids.stderr)}"
    )
    root_pid = list_pids.stdout.splitlines()[0]
    return _run_for_pidfile(
        [str(tmp_path / "trace.d" / f"{root_pid}.bin")],
        cwd=tmp_path,
        env=None,
        timeout=TIMEOUT,
    )


MOTO_RANDOM_SCRIPT = """
from moto.moto_api._internal import mock_random

value = mock_random.get_random_string(length=8)
assert len(value) == 8
print(f"moto-random={value}")
"""


MOTO_S3_SCRIPT = """
import boto3
from botocore.config import Config
from moto import mock_aws

with mock_aws():
    s3 = boto3.client(
        "s3",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(retries={"max_attempts": 1, "mode": "standard"}),
    )
    s3.create_bucket(Bucket="retrace-inprocess-moto")
    s3.put_object(
        Bucket="retrace-inprocess-moto",
        Key="invoice.txt",
        Body=b"total=42",
    )
    response = s3.get_object(
        Bucket="retrace-inprocess-moto",
        Key="invoice.txt",
    )
    assert response["Body"].read() == b"total=42"
print("moto-s3-ok")
"""


def test_moto_random_string_works_without_retrace(tmp_path: Path) -> None:
    pytest.importorskip("moto")

    dryrun = _run_python(tmp_path, "moto_random.py", MOTO_RANDOM_SCRIPT)

    assert dryrun.returncode == 0, (
        f"dryrun failed\nstdout:\n{tail(dryrun.stdout)}\n"
        f"stderr:\n{tail(dryrun.stderr)}"
    )
    assert "moto-random=" in dryrun.stdout


def test_moto_random_string_records_under_retrace(tmp_path: Path) -> None:
    pytest.importorskip("moto")

    _recording, record = _record_script(
        tmp_path,
        "moto_random.py",
        MOTO_RANDOM_SCRIPT,
    )

    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\n"
        f"stderr:\n{tail(record.stderr)}"
    )
    assert "moto-random=" in record.stdout


def test_moto_mock_aws_s3_works_without_retrace(tmp_path: Path) -> None:
    pytest.importorskip("boto3")
    pytest.importorskip("moto")

    dryrun = _run_python(tmp_path, "moto_s3.py", MOTO_S3_SCRIPT)

    assert dryrun.returncode == 0, (
        f"dryrun failed\nstdout:\n{tail(dryrun.stdout)}\n"
        f"stderr:\n{tail(dryrun.stderr)}"
    )
    assert "moto-s3-ok" in dryrun.stdout


def test_moto_mock_aws_s3_records_and_replays(tmp_path: Path) -> None:
    pytest.importorskip("boto3")
    pytest.importorskip("moto")

    recording, record = _record_script(tmp_path, "moto_s3.py", MOTO_S3_SCRIPT)

    assert record.returncode == 0, (
        f"record failed\nstdout:\n{tail(record.stdout)}\n"
        f"stderr:\n{tail(record.stderr)}"
    )
    assert "moto-s3-ok" in record.stdout

    replay = _extract_and_replay(tmp_path, recording)

    assert replay.returncode == 0, (
        f"replay failed\nstdout:\n{tail(replay.stdout)}\n"
        f"stderr:\n{tail(replay.stderr)}"
    )
    assert "moto-s3-ok" in replay.stdout
