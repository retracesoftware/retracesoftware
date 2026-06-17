"""Unit-level smoke coverage for the design-partner pytest stack."""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path
import runpy
import sys

import pytest


def test_design_partner_pytest_plugin_entry_points_are_available():
    for module_name in (
        "pytest_env.plugin",
        "pytest_mock",
        "pytest_randomly",
        "pytest_sugar",
        "teamcity.pytest_plugin",
    ):
        pytest.importorskip(module_name)

    pytest11 = {entry.name: entry.value for entry in entry_points(group="pytest11")}

    assert pytest11["env"] == "pytest_env.plugin"
    assert pytest11["pytest_mock"] == "pytest_mock"
    assert pytest11["randomly"] == "pytest_randomly"
    assert pytest11["sugar"] == "pytest_sugar"
    assert pytest11["pytest-teamcity"] == "teamcity.pytest_plugin"


def test_coverage_api_starts_and_collects_executed_python(tmp_path: Path):
    coverage = pytest.importorskip("coverage")
    target = tmp_path / "coverage_target.py"
    target.write_text("VALUE = 6 * 7\n", encoding="utf-8")

    cov = coverage.Coverage(data_file=None)
    cov.start()
    try:
        namespace = runpy.run_path(str(target))
    finally:
        cov.stop()
        cov.save()

    assert namespace["VALUE"] == 42
    measured = {filename for filename in cov.get_data().measured_files()}
    assert str(target) in measured


def test_moto_mock_aws_s3_api_smoke():
    boto3 = pytest.importorskip("boto3")
    moto = pytest.importorskip("moto")

    with moto.mock_aws():
        s3 = boto3.client(
            "s3",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
        s3.create_bucket(Bucket="retrace-unit-smoke")
        s3.put_object(Bucket="retrace-unit-smoke", Key="sample.txt", Body=b"ok")

        response = s3.get_object(Bucket="retrace-unit-smoke", Key="sample.txt")

    assert response["Body"].read() == b"ok"


def test_aiosmtpd_controller_api_is_importable():
    controller = pytest.importorskip("aiosmtpd.controller")
    smtp = pytest.importorskip("aiosmtpd.smtp")

    assert hasattr(controller, "Controller")
    assert hasattr(smtp, "SMTP")


def test_teamcity_messages_api_is_importable():
    messages = pytest.importorskip("teamcity.messages")

    assert hasattr(messages, "TeamcityServiceMessages")


def test_design_partner_stack_uses_supported_python_for_retrace():
    assert sys.version_info[:2] in {(3, 11), (3, 12)}
