"""Record/replay coverage for the design-partner pytest dependency stack."""

from __future__ import annotations

from pathlib import Path
import os

import pytest

from tests.helpers import PYTHON, _run_for_pidfile, tail
from tests.install.external._pytest_replay_regression_helpers import (
    assert_successful_replay,
    clean_env,
    minimal_project_pythonpath,
    record_extract_replay_pytest,
    write_files,
)


TIMEOUT = 60


def _record_extract_replay_command(
    tmp_path: Path,
    *,
    files: dict[str, str],
    command: list[str],
    env: dict[str, str] | None = None,
    replay_env: dict[str, str] | None = None,
):
    write_files(tmp_path, files)
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
            *command,
        ],
        cwd=tmp_path,
        env=clean_env(tmp_path, env),
        timeout=TIMEOUT,
    )
    assert recording.exists(), (
        f"recording was not created\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr:\n{tail(record.stderr)}"
    )

    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=clean_env(tmp_path, replay_env),
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
        env=clean_env(tmp_path, replay_env),
        timeout=TIMEOUT,
    )
    assert list_pids.returncode == 0, (
        f"list_pids failed\nstdout:\n{tail(list_pids.stdout)}\n"
        f"stderr:\n{tail(list_pids.stderr)}"
    )

    root_pid = list_pids.stdout.splitlines()[0]
    replay = _run_for_pidfile(
        [str(tmp_path / "trace.d" / f"{root_pid}.bin")],
        cwd=tmp_path,
        env=clean_env(tmp_path, replay_env),
        timeout=TIMEOUT,
    )
    return record, replay


def _plugin_env(tmp_path: Path, *plugins: str) -> dict[str, str]:
    return {
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_PLUGINS": ",".join(plugins),
        "PYTHONPATH": minimal_project_pythonpath(tmp_path),
    }


def test_pytest_env_pyproject_values_replay(tmp_path: Path) -> None:
    pytest.importorskip("pytest_env.plugin")

    files = {
        "pyproject.toml": """
            [tool.pytest.ini_options]
            env = [
              "DB1_UID=sa",
              "DB1_PWD=pwd12345!",
              "DB1_SERVER=172.17.0.1",
              "EMAIL_HOST=localhost",
              "EMAIL_PORT=8025",
              "ORION_ALT_EMAIL=internal@email.com",
            ]
        """,
        "tests/test_env_config.py": """
            import os


            def test_pytest_env_values_are_available():
                assert os.environ["DB1_UID"] == "sa"
                assert os.environ["DB1_PWD"] == "pwd12345!"
                assert os.environ["DB1_SERVER"] == "172.17.0.1"
                assert os.environ["EMAIL_HOST"] == "localhost"
                assert os.environ["EMAIL_PORT"] == "8025"
                assert os.environ["ORION_ALT_EMAIL"] == "internal@email.com"
        """,
    }
    env = _plugin_env(tmp_path, "pytest_env.plugin")
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_env_config.py::test_pytest_env_values_are_available", "-q"],
        env=env,
        replay_env=env,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_pytest_mock_randomly_design_partner_plugins_replay(tmp_path: Path) -> None:
    pytest.importorskip("pytest_mock")
    pytest.importorskip("pytest_randomly")

    files = {
        "tests/test_mock_randomly.py": """
            def test_mock_and_random_ordering(mocker):
                callback = mocker.Mock(return_value={"total": 42})
                assert callback()["total"] == 42
                callback.assert_called_once_with()
        """,
    }
    env = _plugin_env(tmp_path, "pytest_mock", "pytest_randomly")
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_mock_randomly.py::test_mock_and_random_ordering",
            "-q",
            "--randomly-seed=12345",
        ],
        env=env,
        replay_env=env,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_pytest_sugar_terminal_plugin_replays(tmp_path: Path) -> None:
    pytest.importorskip("pytest_sugar")

    files = {
        "tests/test_sugar.py": """
            def test_sugar_output_path():
                assert sum([2, 3, 5]) == 10
        """,
    }
    env = _plugin_env(tmp_path, "pytest_sugar")
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_sugar.py::test_sugar_output_path", "-q"],
        env=env,
        replay_env=env,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_teamcity_messages_plugin_replays_service_output(tmp_path: Path) -> None:
    pytest.importorskip("teamcity.pytest_plugin")

    files = {
        "tests/test_teamcity.py": """
            def test_teamcity_service_message_path():
                assert "service".upper() == "SERVICE"
        """,
    }
    env = {
        **_plugin_env(tmp_path, "teamcity.pytest_plugin"),
        "TEAMCITY_VERSION": "2024.1",
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_teamcity.py::test_teamcity_service_message_path", "-q"],
        env=env,
        replay_env=env,
    )

    assert "##teamcity[testStarted" in record.stdout
    assert_successful_replay(record, replay, "1 passed")


def test_moto_inprocess_s3_mock_replays(tmp_path: Path) -> None:
    pytest.importorskip("boto3")
    pytest.importorskip("moto")

    files = {
        "tests/test_moto_s3.py": """
            import boto3
            from moto import mock_aws


            def test_moto_s3_roundtrip():
                with mock_aws():
                    s3 = boto3.client(
                        "s3",
                        region_name="us-east-1",
                        aws_access_key_id="test",
                        aws_secret_access_key="test",
                    )
                    s3.create_bucket(Bucket="retrace-design-partner")
                    s3.put_object(
                        Bucket="retrace-design-partner",
                        Key="invoice.txt",
                        Body=b"total=42",
                    )
                    response = s3.get_object(
                        Bucket="retrace-design-partner",
                        Key="invoice.txt",
                    )
                    assert response["Body"].read() == b"total=42"
        """,
    }
    env = {
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": minimal_project_pythonpath(tmp_path),
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": "us-east-1",
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_moto_s3.py::test_moto_s3_roundtrip", "-q"],
        env=env,
        replay_env=env,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_aiosmtpd_local_smtp_roundtrip_replays(tmp_path: Path) -> None:
    pytest.importorskip("aiosmtpd.controller")

    files = {
        "tests/test_smtp.py": """
            import socket
            import smtplib

            from aiosmtpd.controller import Controller


            class Handler:
                def __init__(self):
                    self.messages = []

                async def handle_DATA(self, server, session, envelope):
                    self.messages.append(envelope.content.decode("utf-8"))
                    return "250 Message accepted"


            def free_port():
                sock = socket.socket()
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
                sock.close()
                return port


            def test_aiosmtpd_roundtrip():
                handler = Handler()
                port = free_port()
                controller = Controller(handler, hostname="127.0.0.1", port=port)
                controller.start()
                try:
                    with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
                        client.sendmail(
                            "sender@example.com",
                            ["receiver@example.com"],
                            "Subject: Retrace\\n\\nhello design partner",
                        )
                    assert len(handler.messages) == 1
                    assert "hello design partner" in handler.messages[0]
                finally:
                    controller.stop()
        """,
    }
    env = {
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": minimal_project_pythonpath(tmp_path),
    }
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_smtp.py::test_aiosmtpd_roundtrip", "-q"],
        env=env,
        replay_env=env,
        timeout=TIMEOUT,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_coverage_run_pytest_replays(tmp_path: Path) -> None:
    pytest.importorskip("coverage")

    files = {
        "samplepkg/__init__.py": "",
        "samplepkg/calc.py": """
            def total(values):
                return sum(values)
        """,
        "tests/test_calc.py": """
            from samplepkg.calc import total


            def test_total():
                assert total([10, 20, 12]) == 42
        """,
    }
    env = {
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": minimal_project_pythonpath(tmp_path),
    }
    record, replay = _record_extract_replay_command(
        tmp_path,
        files=files,
        command=[
            "-m",
            "coverage",
            "run",
            "-m",
            "pytest",
            "tests/test_calc.py::test_total",
            "-q",
        ],
        env=env,
        replay_env=env,
    )

    assert_successful_replay(record, replay, "1 passed")


@pytest.mark.skipif(
    os.environ.get("RETRACE_RUN_DESIGN_PARTNER_FULL_STACK") != "1",
    reason="set RETRACE_RUN_DESIGN_PARTNER_FULL_STACK=1 to run the full local stack check",
)
def test_full_design_partner_stack_local_replay_check(tmp_path: Path) -> None:
    for module_name in (
        "aiosmtpd.controller",
        "boto3",
        "coverage",
        "moto",
        "pytest_env.plugin",
        "pytest_mock",
        "pytest_randomly",
        "pytest_sugar",
        "teamcity.pytest_plugin",
    ):
        pytest.importorskip(module_name)

    files = {
        "pyproject.toml": """
            [tool.pytest.ini_options]
            env = [
              "DB1_UID=sa",
              "DB1_PWD=pwd12345!",
              "DB1_SERVER=172.17.0.1",
              "DB2_UID=sa",
              "DB2_PWD=pwd12345!",
              "DB2_SERVER=172.17.0.1",
              "EMAIL_HOST=localhost",
              "EMAIL_PORT=8025",
              "ORION_ALT_EMAIL=internal@email.com",
            ]
        """,
        "tests/test_full_stack.py": """
            import os
            import smtplib

            import boto3
            from aiosmtpd.controller import Controller
            from moto import mock_aws


            class Handler:
                def __init__(self):
                    self.messages = []

                async def handle_DATA(self, server, session, envelope):
                    self.messages.append(envelope.content.decode("utf-8"))
                    return "250 Message accepted"


            def test_design_partner_full_stack(mocker):
                assert os.environ["DB1_UID"] == "sa"
                assert os.environ["DB2_SERVER"] == "172.17.0.1"
                callback = mocker.Mock(return_value="ok")
                assert callback() == "ok"

                with mock_aws():
                    s3 = boto3.client(
                        "s3",
                        region_name="us-east-1",
                        aws_access_key_id="test",
                        aws_secret_access_key="test",
                    )
                    s3.create_bucket(Bucket="retrace-full-stack")
                    s3.put_object(
                        Bucket="retrace-full-stack",
                        Key="message.txt",
                        Body=b"hello",
                    )
                    assert s3.get_object(
                        Bucket="retrace-full-stack",
                        Key="message.txt",
                    )["Body"].read() == b"hello"

                def free_port():
                    import socket

                    sock = socket.socket()
                    sock.bind(("127.0.0.1", 0))
                    port = sock.getsockname()[1]
                    sock.close()
                    return port


                handler = Handler()
                controller = Controller(handler, hostname="127.0.0.1", port=free_port())
                controller.start()
                try:
                    port = controller.port
                    with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
                        client.sendmail(
                            "sender@example.com",
                            ["receiver@example.com"],
                            "Subject: Retrace\\n\\nfull stack",
                        )
                    assert "full stack" in handler.messages[0]
                finally:
                    controller.stop()
        """,
    }
    env = {
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTEST_PLUGINS": ",".join(
            [
                "pytest_env.plugin",
                "pytest_mock",
                "pytest_randomly",
                "pytest_sugar",
                "teamcity.pytest_plugin",
            ]
        ),
        "PYTHONPATH": minimal_project_pythonpath(tmp_path),
        "TEAMCITY_VERSION": "2024.1",
        "AWS_EC2_METADATA_DISABLED": "true",
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_DEFAULT_REGION": "us-east-1",
    }
    record, replay = _record_extract_replay_command(
        tmp_path,
        files=files,
        command=[
            "-m",
            "coverage",
            "run",
            "-m",
            "pytest",
            "tests/test_full_stack.py::test_design_partner_full_stack",
            "-q",
            "--randomly-seed=12345",
        ],
        env=env,
        replay_env=env,
    )

    assert "##teamcity[testStarted" in record.stdout
    assert_successful_replay(record, replay, "1 passed")
