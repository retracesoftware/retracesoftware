"""Design-partner pytest dependency stack replay matrix.

These tests intentionally cover the screenshot dependency set in small
combinations before the full stack:

- aiosmtpd
- coverage
- pytest
- pytest-mock
- pytest-randomly
- pytest-sugar
- pytest-env
- teamcity-messages
- moto
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.helpers import PYTHON, _run_for_pidfile, tail
from tests.install.external._pytest_replay_regression_helpers import (
    assert_successful_replay,
    clean_env,
    minimal_project_pythonpath,
    record_extract_replay_pytest,
    write_files,
)


TIMEOUT = 90


ENV_PYPROJECT = """
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
"""


def _require_modules(*module_names: str) -> None:
    for module_name in module_names:
        pytest.importorskip(module_name)


def _plugin_env(
    tmp_path: Path,
    *plugins: str,
    autoload: bool = False,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    env = {
        "PYTHONPATH": minimal_project_pythonpath(tmp_path),
    }
    if not autoload:
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        env["PYTEST_PLUGINS"] = ",".join(plugins)
    if extra:
        env.update(extra)
    return env


def _record_extract_replay_command(
    tmp_path: Path,
    *,
    files: dict[str, str],
    command: list[str],
    env: dict[str, str],
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

    replay_env = clean_env(tmp_path, env)
    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=tmp_path,
        env=replay_env,
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
        env=replay_env,
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
        env=replay_env,
        timeout=TIMEOUT,
    )
    return record, replay


SCENARIOS = [
    pytest.param(
        "pytest-env",
        ("pytest_env.plugin",),
        ("pytest_env.plugin",),
        {
            "pyproject.toml": ENV_PYPROJECT,
            "tests/test_env.py": """
                import os


                def test_env_values_from_pyproject():
                    assert os.environ["DB1_UID"] == "sa"
                    assert os.environ["DB2_SERVER"] == "172.17.0.1"
                    assert os.environ["EMAIL_PORT"] == "8025"
            """,
        },
        ["tests/test_env.py::test_env_values_from_pyproject", "-q"],
        {},
        "1 passed",
        id="single-pytest-env",
    ),
    pytest.param(
        "pytest-mock",
        ("pytest_mock",),
        ("pytest_mock",),
        {
            "tests/test_mock.py": """
                def test_mock_patch_and_spy(mocker):
                    values = []
                    callback = mocker.Mock(side_effect=lambda item: values.append(item))
                    callback("invoice")
                    callback.assert_called_once_with("invoice")
                    assert values == ["invoice"]
            """,
        },
        ["tests/test_mock.py::test_mock_patch_and_spy", "-q"],
        {},
        "1 passed",
        id="single-pytest-mock",
    ),
    pytest.param(
        "pytest-randomly",
        ("pytest_randomly",),
        ("pytest_randomly",),
        {
            "tests/test_randomly.py": """
                def test_alpha():
                    assert sorted([3, 1, 2]) == [1, 2, 3]


                def test_beta():
                    assert "retrace".upper() == "RETRACE"
            """,
        },
        ["tests/test_randomly.py", "-q", "--randomly-seed=12345"],
        {},
        "2 passed",
        id="single-pytest-randomly",
    ),
    pytest.param(
        "pytest-sugar",
        ("pytest_sugar",),
        ("pytest_sugar",),
        {
            "tests/test_sugar.py": """
                def test_terminal_progress_plugin():
                    assert sum([10, 20, 12]) == 42
            """,
        },
        ["tests/test_sugar.py::test_terminal_progress_plugin", "-q"],
        {},
        "1 passed",
        id="single-pytest-sugar",
    ),
    pytest.param(
        "teamcity-messages",
        ("teamcity.pytest_plugin",),
        ("teamcity.pytest_plugin",),
        {
            "tests/test_teamcity.py": """
                def test_teamcity_service_messages():
                    assert "teamcity".startswith("team")
            """,
        },
        ["tests/test_teamcity.py::test_teamcity_service_messages", "-q"],
        {"TEAMCITY_VERSION": "2024.1"},
        "1 passed",
        id="single-teamcity-messages",
    ),
    pytest.param(
        "pytest-env+pytest-mock",
        ("pytest_env.plugin", "pytest_mock"),
        ("pytest_env.plugin", "pytest_mock"),
        {
            "pyproject.toml": ENV_PYPROJECT,
            "tests/test_env_mock.py": """
                import os


                def test_env_and_mock(mocker):
                    callback = mocker.Mock(return_value=os.environ["DB1_UID"])
                    assert callback() == "sa"
                    callback.assert_called_once_with()
            """,
        },
        ["tests/test_env_mock.py::test_env_and_mock", "-q"],
        {},
        "1 passed",
        id="pair-env-mock",
    ),
    pytest.param(
        "pytest-mock+pytest-randomly",
        ("pytest_mock", "pytest_randomly"),
        ("pytest_mock", "pytest_randomly"),
        {
            "tests/test_mock_randomly.py": """
                def test_mock_under_randomly_one(mocker):
                    fn = mocker.Mock(return_value=21)
                    assert fn() * 2 == 42


                def test_mock_under_randomly_two(mocker):
                    fn = mocker.Mock(return_value="ok")
                    assert fn() == "ok"
            """,
        },
        ["tests/test_mock_randomly.py", "-q", "--randomly-seed=12345"],
        {},
        "2 passed",
        id="pair-mock-randomly",
    ),
    pytest.param(
        "pytest-env+pytest-randomly",
        ("pytest_env.plugin", "pytest_randomly"),
        ("pytest_env.plugin", "pytest_randomly"),
        {
            "pyproject.toml": ENV_PYPROJECT,
            "tests/test_env_randomly.py": """
                import os


                def test_env_randomly_one():
                    assert os.environ["DB1_UID"] == "sa"


                def test_env_randomly_two():
                    assert os.environ["EMAIL_PORT"] == "8025"
            """,
        },
        ["tests/test_env_randomly.py", "-q", "--randomly-seed=12345"],
        {},
        "2 passed",
        id="pair-env-randomly",
    ),
    pytest.param(
        "pytest-sugar+pytest-randomly",
        ("pytest_sugar", "pytest_randomly"),
        ("pytest_sugar", "pytest_randomly"),
        {
            "tests/test_sugar_randomly.py": """
                def test_sugar_randomly_one():
                    assert len("abc") == 3


                def test_sugar_randomly_two():
                    assert {"total": 42}["total"] == 42
            """,
        },
        ["tests/test_sugar_randomly.py", "-q", "--randomly-seed=12345"],
        {},
        "2 passed",
        id="pair-sugar-randomly",
    ),
    pytest.param(
        "teamcity-messages+pytest-randomly",
        ("teamcity.pytest_plugin", "pytest_randomly"),
        ("teamcity.pytest_plugin", "pytest_randomly"),
        {
            "tests/test_teamcity_randomly.py": """
                def test_teamcity_randomly_one():
                    assert "teamcity".startswith("team")


                def test_teamcity_randomly_two():
                    assert "randomly".endswith("ly")
            """,
        },
        ["tests/test_teamcity_randomly.py", "-q", "--randomly-seed=12345"],
        {"TEAMCITY_VERSION": "2024.1"},
        "2 passed",
        id="pair-teamcity-randomly",
    ),
    pytest.param(
        "pytest-sugar+teamcity-messages",
        ("pytest_sugar", "teamcity.pytest_plugin"),
        ("pytest_sugar", "teamcity.pytest_plugin"),
        {
            "tests/test_sugar_teamcity.py": """
                def test_terminal_plugins_together():
                    assert "-".join(["a", "b"]) == "a-b"
            """,
        },
        ["tests/test_sugar_teamcity.py::test_terminal_plugins_together", "-q"],
        {"TEAMCITY_VERSION": "2024.1"},
        "1 passed",
        id="pair-sugar-teamcity",
    ),
    pytest.param(
        "env+mock+randomly",
        ("pytest_env.plugin", "pytest_mock", "pytest_randomly"),
        ("pytest_env.plugin", "pytest_mock", "pytest_randomly"),
        {
            "pyproject.toml": ENV_PYPROJECT,
            "tests/test_env_mock_randomly.py": """
                import os


                def test_env_mock_randomly_a(mocker):
                    fn = mocker.Mock(return_value=os.environ["DB2_UID"])
                    assert fn() == "sa"


                def test_env_mock_randomly_b():
                    assert os.environ["ORION_ALT_EMAIL"].endswith("@email.com")
            """,
        },
        ["tests/test_env_mock_randomly.py", "-q", "--randomly-seed=12345"],
        {},
        "2 passed",
        id="triple-env-mock-randomly",
    ),
    pytest.param(
        "sugar+teamcity+randomly",
        ("pytest_sugar", "teamcity.pytest_plugin", "pytest_randomly"),
        ("pytest_sugar", "teamcity.pytest_plugin", "pytest_randomly"),
        {
            "tests/test_terminal_randomly.py": """
                def test_terminal_randomly_a():
                    assert len("abc") == 3


                def test_terminal_randomly_b():
                    assert {"total": 42}["total"] == 42
            """,
        },
        ["tests/test_terminal_randomly.py", "-q", "--randomly-seed=12345"],
        {"TEAMCITY_VERSION": "2024.1"},
        "2 passed",
        id="triple-sugar-teamcity-randomly",
    ),
]


@pytest.mark.parametrize(
    "name,plugins,required_modules,files,pytest_args,extra_env,expected",
    SCENARIOS,
)
def test_design_partner_pytest_plugin_matrix_replays(
    tmp_path: Path,
    name: str,
    plugins: tuple[str, ...],
    required_modules: tuple[str, ...],
    files: dict[str, str],
    pytest_args: list[str],
    extra_env: dict[str, str],
    expected: str,
) -> None:
    _require_modules(*required_modules)

    env = _plugin_env(tmp_path, *plugins, extra=extra_env)
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=pytest_args,
        env=env,
        replay_env=env,
        timeout=TIMEOUT,
    )

    assert_successful_replay(record, replay, expected)


def test_design_partner_aiosmtpd_roundtrip_replays(tmp_path: Path) -> None:
    _require_modules("aiosmtpd.controller")

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


            def test_smtp_roundtrip():
                handler = Handler()
                port = free_port()
                controller = Controller(handler, hostname="127.0.0.1", port=port)
                controller.start()
                try:
                    with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
                        client.sendmail(
                            "sender@example.com",
                            ["receiver@example.com"],
                            "Subject: Retrace\\n\\nhello",
                        )
                    assert len(handler.messages) == 1
                    assert "hello" in handler.messages[0]
                finally:
                    controller.stop()
        """,
    }
    env = _plugin_env(tmp_path)
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_smtp.py::test_smtp_roundtrip", "-q"],
        env=env,
        replay_env=env,
        timeout=TIMEOUT,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_design_partner_aiosmtpd_with_randomly_replays(tmp_path: Path) -> None:
    _require_modules("aiosmtpd.controller", "pytest_randomly")

    files = {
        "tests/test_smtp_randomly.py": """
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


            def test_smtp_randomly_roundtrip():
                handler = Handler()
                port = free_port()
                controller = Controller(handler, hostname="127.0.0.1", port=port)
                controller.start()
                try:
                    with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
                        client.sendmail(
                            "sender@example.com",
                            ["receiver@example.com"],
                            "Subject: Retrace\\n\\nrandomly smtp",
                        )
                    assert "randomly smtp" in handler.messages[0]
                finally:
                    controller.stop()
        """,
    }
    env = _plugin_env(tmp_path, "pytest_randomly")
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_smtp_randomly.py::test_smtp_randomly_roundtrip",
            "-q",
            "--randomly-seed=12345",
        ],
        env=env,
        replay_env=env,
        timeout=TIMEOUT,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_design_partner_moto_s3_mock_aws_replays(tmp_path: Path) -> None:
    _require_modules("boto3", "moto")

    files = {
        "tests/test_moto.py": """
            import boto3
            from botocore.config import Config
            from moto import mock_aws


            def test_moto_s3_roundtrip():
                with mock_aws():
                    s3 = boto3.client(
                        "s3",
                        region_name="us-east-1",
                        aws_access_key_id="test",
                        aws_secret_access_key="test",
                        config=Config(retries={"max_attempts": 1, "mode": "standard"}),
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
    env = _plugin_env(
        tmp_path,
        extra={
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_DEFAULT_REGION": "us-east-1",
        },
    )
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_moto.py::test_moto_s3_roundtrip", "-q"],
        env=env,
        replay_env=env,
        timeout=TIMEOUT,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_design_partner_moto_with_randomly_replays(tmp_path: Path) -> None:
    _require_modules("boto3", "moto", "pytest_randomly")

    files = {
        "tests/test_moto_randomly.py": """
            import boto3
            from botocore.config import Config
            from moto import mock_aws


            def test_moto_randomly_s3_roundtrip():
                with mock_aws():
                    s3 = boto3.client(
                        "s3",
                        region_name="us-east-1",
                        aws_access_key_id="test",
                        aws_secret_access_key="test",
                        config=Config(retries={"max_attempts": 1, "mode": "standard"}),
                    )
                    s3.create_bucket(Bucket="retrace-design-partner-randomly")
                    s3.put_object(
                        Bucket="retrace-design-partner-randomly",
                        Key="invoice.txt",
                        Body=b"total=42",
                    )
                    response = s3.get_object(
                        Bucket="retrace-design-partner-randomly",
                        Key="invoice.txt",
                    )
                    assert response["Body"].read() == b"total=42"
        """,
    }
    env = _plugin_env(
        tmp_path,
        "pytest_randomly",
        extra={
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_DEFAULT_REGION": "us-east-1",
        },
    )
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=[
            "tests/test_moto_randomly.py::test_moto_randomly_s3_roundtrip",
            "-q",
            "--randomly-seed=12345",
        ],
        env=env,
        replay_env=env,
        timeout=TIMEOUT,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_design_partner_coverage_run_pytest_with_env_and_mock_replays(
    tmp_path: Path,
) -> None:
    _require_modules("coverage", "pytest_env.plugin", "pytest_mock")

    files = {
        "pyproject.toml": ENV_PYPROJECT,
        "samplepkg/__init__.py": "",
        "samplepkg/calc.py": """
            def total(values):
                return sum(values)
        """,
        "tests/test_coverage_env_mock.py": """
            import os

            from samplepkg.calc import total


            def test_coverage_env_mock(mocker):
                callback = mocker.Mock(return_value=total([10, 20, 12]))
                assert os.environ["DB1_UID"] == "sa"
                assert callback() == 42
        """,
    }
    env = _plugin_env(tmp_path, "pytest_env.plugin", "pytest_mock")
    record, replay = _record_extract_replay_command(
        tmp_path,
        files=files,
        command=[
            "-m",
            "coverage",
            "run",
            "-m",
            "pytest",
            "tests/test_coverage_env_mock.py::test_coverage_env_mock",
            "-q",
        ],
        env=env,
    )

    assert_successful_replay(record, replay, "1 passed")


def test_design_partner_coverage_run_pytest_with_randomly_replays(
    tmp_path: Path,
) -> None:
    _require_modules("coverage", "pytest_randomly")

    files = {
        "samplepkg/__init__.py": "",
        "samplepkg/calc.py": """
            def total(values):
                return sum(values)
        """,
        "tests/test_coverage_randomly.py": """
            from samplepkg.calc import total


            def test_coverage_randomly_one():
                assert total([10, 20, 12]) == 42


            def test_coverage_randomly_two():
                assert total([1, 2, 3]) == 6
        """,
    }
    env = _plugin_env(tmp_path, "pytest_randomly")
    record, replay = _record_extract_replay_command(
        tmp_path,
        files=files,
        command=[
            "-m",
            "coverage",
            "run",
            "-m",
            "pytest",
            "tests/test_coverage_randomly.py",
            "-q",
            "--randomly-seed=12345",
        ],
        env=env,
    )

    assert_successful_replay(record, replay, "2 passed")


def test_design_partner_normal_autoload_plugin_stack_replays(tmp_path: Path) -> None:
    _require_modules(
        "pytest_env.plugin",
        "pytest_mock",
        "pytest_randomly",
        "pytest_sugar",
        "teamcity.pytest_plugin",
    )

    files = {
        "pyproject.toml": ENV_PYPROJECT,
        "tests/test_autoload_stack.py": """
            import os


            def test_autoload_stack_one(mocker):
                callback = mocker.Mock(return_value=os.environ["DB1_UID"])
                assert callback() == "sa"


            def test_autoload_stack_two():
                assert os.environ["EMAIL_HOST"] == "localhost"
        """,
    }
    env = _plugin_env(
        tmp_path,
        autoload=True,
        extra={
            "TEAMCITY_VERSION": "2024.1",
        },
    )
    record, replay = record_extract_replay_pytest(
        tmp_path,
        files=files,
        pytest_args=["tests/test_autoload_stack.py", "-q", "--randomly-seed=12345"],
        env=env,
        replay_env=env,
        timeout=TIMEOUT,
    )

    assert "##teamcity[testStarted" in record.stdout
    assert_successful_replay(record, replay, "2 passed")


def test_design_partner_full_stack_without_randomly_with_coverage_replays(
    tmp_path: Path,
) -> None:
    _require_modules(
        "aiosmtpd.controller",
        "boto3",
        "coverage",
        "moto",
        "pytest_env.plugin",
        "pytest_mock",
        "pytest_sugar",
        "teamcity.pytest_plugin",
    )

    files = {
        "pyproject.toml": ENV_PYPROJECT,
        "tests/test_full_stack_without_randomly.py": """
            import os
            import socket
            import smtplib

            import boto3
            from aiosmtpd.controller import Controller
            from botocore.config import Config
            from moto import mock_aws


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


            def test_design_partner_full_stack_without_randomly(mocker):
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
                        config=Config(retries={"max_attempts": 1, "mode": "standard"}),
                    )
                    s3.create_bucket(Bucket="retrace-full-stack-no-randomly")
                    s3.put_object(
                        Bucket="retrace-full-stack-no-randomly",
                        Key="message.txt",
                        Body=b"hello",
                    )
                    assert s3.get_object(
                        Bucket="retrace-full-stack-no-randomly",
                        Key="message.txt",
                    )["Body"].read() == b"hello"

                handler = Handler()
                port = free_port()
                controller = Controller(handler, hostname="127.0.0.1", port=port)
                controller.start()
                try:
                    with smtplib.SMTP("127.0.0.1", port, timeout=5) as client:
                        client.sendmail(
                            "sender@example.com",
                            ["receiver@example.com"],
                            "Subject: Retrace\\n\\nfull stack without randomly",
                        )
                    assert "full stack without randomly" in handler.messages[0]
                finally:
                    controller.stop()
        """,
    }
    env = _plugin_env(
        tmp_path,
        "pytest_env.plugin",
        "pytest_mock",
        "pytest_sugar",
        "teamcity.pytest_plugin",
        extra={
            "TEAMCITY_VERSION": "2024.1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_DEFAULT_REGION": "us-east-1",
        },
    )
    record, replay = _record_extract_replay_command(
        tmp_path,
        files=files,
        command=[
            "-m",
            "coverage",
            "run",
            "-m",
            "pytest",
            "tests/test_full_stack_without_randomly.py::test_design_partner_full_stack_without_randomly",
            "-q",
        ],
        env=env,
    )

    assert "##teamcity[testStarted" in record.stdout
    assert_successful_replay(record, replay, "1 passed")


def test_design_partner_full_stack_with_coverage_replays(tmp_path: Path) -> None:
    _require_modules(
        "aiosmtpd.controller",
        "boto3",
        "coverage",
        "moto",
        "pytest_env.plugin",
        "pytest_mock",
        "pytest_randomly",
        "pytest_sugar",
        "teamcity.pytest_plugin",
    )

    files = {
        "pyproject.toml": ENV_PYPROJECT,
        "tests/test_full_stack.py": """
            import os
            import socket
            import smtplib

            import boto3
            from aiosmtpd.controller import Controller
            from botocore.config import Config
            from moto import mock_aws


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
                        config=Config(retries={"max_attempts": 1, "mode": "standard"}),
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

                handler = Handler()
                port = free_port()
                controller = Controller(handler, hostname="127.0.0.1", port=port)
                controller.start()
                try:
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
    env = _plugin_env(
        tmp_path,
        "pytest_env.plugin",
        "pytest_mock",
        "pytest_randomly",
        "pytest_sugar",
        "teamcity.pytest_plugin",
        extra={
            "TEAMCITY_VERSION": "2024.1",
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_DEFAULT_REGION": "us-east-1",
        },
    )
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
    )

    assert "##teamcity[testStarted" in record.stdout
    assert_successful_replay(record, replay, "1 passed")
