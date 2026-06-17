"""Local design-partner pytest dependency matrix runner.

This is intentionally a runner, not a collected pytest test. It builds many
temporary pytest projects and runs each under Retrace record/extract/replay so
we can map compatibility for the pinned design-partner stack.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import itertools
import json
import os
from pathlib import Path
import shutil
import sys
import time

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from tests.helpers import PYTHON, _run_for_pidfile, tail
from tests.install.external._pytest_replay_regression_helpers import (
    clean_env,
    minimal_project_pythonpath,
    write_files,
)


TIMEOUT = 120


PLUGIN_MODULES = {
    "env": "pytest_env.plugin",
    "mock": "pytest_mock",
    "randomly": "pytest_randomly",
    "sugar": "pytest_sugar",
    "teamcity": "teamcity.pytest_plugin",
}

PLUGIN_ORDER = ("env", "mock", "randomly", "sugar", "teamcity")

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


@dataclass(frozen=True)
class Scenario:
    name: str
    plugins: tuple[str, ...]
    libs: tuple[str, ...] = ()
    coverage: bool = False
    autoload: bool = False


def _scenario_env(tmp_path: Path, scenario: Scenario) -> dict[str, str]:
    env = {
        "PYTHONPATH": minimal_project_pythonpath(tmp_path),
    }
    if not scenario.autoload:
        env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
        env["PYTEST_PLUGINS"] = ",".join(
            PLUGIN_MODULES[name] for name in scenario.plugins
        )
    if "teamcity" in scenario.plugins or scenario.autoload:
        env["TEAMCITY_VERSION"] = "2024.1"
    if "moto" in scenario.libs:
        env.update(
            {
                "AWS_EC2_METADATA_DISABLED": "true",
                "AWS_ACCESS_KEY_ID": "test",
                "AWS_SECRET_ACCESS_KEY": "test",
                "AWS_DEFAULT_REGION": "us-east-1",
            }
        )
    return env


def _test_source(scenario: Scenario) -> str:
    wants_mock = "mock" in scenario.plugins
    args = "mocker" if wants_mock else ""
    lines: list[str] = [
        "import os",
        "import socket",
        "import smtplib",
        "",
    ]

    if "moto" in scenario.libs:
        lines.extend(
            [
                "import boto3",
                "from botocore.config import Config",
                "from moto import mock_aws",
                "",
            ]
        )

    if "aiosmtpd" in scenario.libs:
        lines.extend(
            [
                "from aiosmtpd.controller import Controller",
                "",
                "",
                "class Handler:",
                "    def __init__(self):",
                "        self.messages = []",
                "",
                "    async def handle_DATA(self, server, session, envelope):",
                "        self.messages.append(envelope.content.decode('utf-8'))",
                "        return '250 Message accepted'",
                "",
                "",
                "def free_port():",
                "    sock = socket.socket()",
                "    sock.bind(('127.0.0.1', 0))",
                "    port = sock.getsockname()[1]",
                "    sock.close()",
                "    return port",
                "",
            ]
        )

    lines.extend(
        [
            "",
            f"def test_design_partner_one({args}):",
            "    assert 'retrace'.upper() == 'RETRACE'",
        ]
    )
    if "env" in scenario.plugins or scenario.autoload:
        lines.extend(
            [
                "    assert os.environ['DB1_UID'] == 'sa'",
                "    assert os.environ['EMAIL_PORT'] == '8025'",
            ]
        )
    if wants_mock:
        lines.extend(
            [
                "    callback = mocker.Mock(return_value={'total': 42})",
                "    assert callback()['total'] == 42",
                "    callback.assert_called_once_with()",
            ]
        )
    if "moto" in scenario.libs:
        lines.extend(
            [
                "    with mock_aws():",
                "        s3 = boto3.client(",
                "            's3',",
                "            region_name='us-east-1',",
                "            aws_access_key_id='test',",
                "            aws_secret_access_key='test',",
                "            config=Config(retries={'max_attempts': 1, 'mode': 'standard'}),",
                "        )",
                "        s3.create_bucket(Bucket='retrace-design-partner-matrix')",
                "        s3.put_object(",
                "            Bucket='retrace-design-partner-matrix',",
                "            Key='message.txt',",
                "            Body=b'total=42',",
                "        )",
                "        body = s3.get_object(",
                "            Bucket='retrace-design-partner-matrix',",
                "            Key='message.txt',",
                "        )['Body'].read()",
                "        assert body == b'total=42'",
            ]
        )
    if "aiosmtpd" in scenario.libs:
        lines.extend(
            [
                "    handler = Handler()",
                "    port = free_port()",
                "    controller = Controller(handler, hostname='127.0.0.1', port=port)",
                "    controller.start()",
                "    try:",
                "        with smtplib.SMTP('127.0.0.1', port, timeout=5) as client:",
                "            client.sendmail(",
                "                'sender@example.com',",
                "                ['receiver@example.com'],",
                "                'Subject: Retrace\\n\\nhello matrix',",
                "            )",
                "        assert 'hello matrix' in handler.messages[0]",
                "    finally:",
                "        controller.stop()",
            ]
        )

    if "randomly" in scenario.plugins or scenario.autoload:
        lines.extend(
            [
                "",
                "",
                "def test_design_partner_two():",
                "    assert sorted([3, 1, 2]) == [1, 2, 3]",
            ]
        )

    return "\n".join(lines) + "\n"


def _scenario_files(scenario: Scenario) -> dict[str, str]:
    files = {"tests/test_design_partner_matrix.py": _test_source(scenario)}
    if "env" in scenario.plugins or scenario.autoload:
        files["pyproject.toml"] = ENV_PYPROJECT
    return files


def _pytest_command(scenario: Scenario) -> list[str]:
    pytest_args = ["tests/test_design_partner_matrix.py", "-q"]
    if "randomly" in scenario.plugins or scenario.autoload:
        pytest_args.append("--randomly-seed=12345")
    if scenario.coverage:
        return ["-m", "coverage", "run", "-m", "pytest", *pytest_args]
    return ["-m", "pytest", *pytest_args]


def _classify(text: str) -> str:
    if "Checkpoint difference:" in text:
        for line in text.splitlines():
            if "Checkpoint difference:" in line:
                return line.strip()
        return "Checkpoint difference"
    if "bind marker returned" in text:
        return "bind marker returned"
    if "Could not read:" in text:
        return "Could not read"
    if "Exec format error" in text:
        return "Exec format error"
    if "replay timed out" in text or "TIMEOUT" in text:
        return "timeout"
    if "INTERNALERROR>" in text:
        return "pytest internal error"
    if "Traceback" in text:
        return "traceback"
    return "unknown failure"


def _record_extract_replay(root: Path, scenario: Scenario) -> dict[str, object]:
    files = _scenario_files(scenario)
    write_files(root, files)
    recording = root / "trace.retrace"
    env = clean_env(root, _scenario_env(root, scenario))

    started = time.time()
    record = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--stacktraces",
            "--",
            *_pytest_command(scenario),
        ],
        cwd=root,
        env=env,
        timeout=TIMEOUT,
    )
    if record.returncode != 0 or not recording.exists():
        combined = record.stdout + record.stderr
        return {
            "ok": False,
            "stage": "record",
            "signature": _classify(combined),
            "duration": round(time.time() - started, 3),
            "root": str(root),
            "stdout_tail": tail(record.stdout, 3000),
            "stderr_tail": tail(record.stderr, 3000),
        }

    extract = _run_for_pidfile(
        [str(recording), "--extract"],
        cwd=root,
        env=env,
        timeout=TIMEOUT,
    )
    if extract.returncode != 0:
        combined = extract.stdout + extract.stderr
        return {
            "ok": False,
            "stage": "extract",
            "signature": _classify(combined),
            "duration": round(time.time() - started, 3),
            "root": str(root),
            "stdout_tail": tail(extract.stdout, 3000),
            "stderr_tail": tail(extract.stderr, 3000),
        }

    list_pids = _run_for_pidfile(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=root,
        env=env,
        timeout=TIMEOUT,
    )
    if list_pids.returncode != 0 or not list_pids.stdout.splitlines():
        combined = list_pids.stdout + list_pids.stderr
        return {
            "ok": False,
            "stage": "list_pids",
            "signature": _classify(combined),
            "duration": round(time.time() - started, 3),
            "root": str(root),
            "stdout_tail": tail(list_pids.stdout, 3000),
            "stderr_tail": tail(list_pids.stderr, 3000),
        }

    root_pid = list_pids.stdout.splitlines()[0]
    replay = _run_for_pidfile(
        [str(root / "trace.d" / f"{root_pid}.bin")],
        cwd=root,
        env=env,
        timeout=TIMEOUT,
    )
    combined = replay.stdout + replay.stderr
    ok = (
        replay.returncode == 0
        and "Checkpoint difference:" not in combined
        and "Could not read:" not in combined
        and "bind marker returned" not in combined
    )
    return {
        "ok": ok,
        "stage": "replay",
        "signature": "ok" if ok else _classify(combined),
        "duration": round(time.time() - started, 3),
        "root": str(root),
        "stdout_tail": tail(replay.stdout, 3000),
        "stderr_tail": tail(replay.stderr, 3000),
    }


def _all_plugin_combos() -> list[tuple[str, ...]]:
    combos: list[tuple[str, ...]] = []
    for size in range(1, len(PLUGIN_ORDER) + 1):
        combos.extend(itertools.combinations(PLUGIN_ORDER, size))
    return combos


def build_scenarios() -> list[Scenario]:
    scenarios: list[Scenario] = []
    for combo in _all_plugin_combos():
        name = "plugins-" + "-".join(combo)
        scenarios.append(Scenario(name=name, plugins=combo))
        scenarios.append(Scenario(name="coverage-" + name, plugins=combo, coverage=True))

    heavy_plugin_groups = [
        (),
        ("randomly",),
        ("env", "mock", "sugar", "teamcity"),
        ("env", "mock", "randomly", "sugar", "teamcity"),
    ]
    for libs in (("moto",), ("aiosmtpd",), ("moto", "aiosmtpd")):
        for plugins in heavy_plugin_groups:
            stem = "-".join(libs) + ("-" + "-".join(plugins) if plugins else "-plain")
            scenarios.append(Scenario(name="libs-" + stem, plugins=plugins, libs=libs))
            scenarios.append(
                Scenario(
                    name="coverage-libs-" + stem,
                    plugins=plugins,
                    libs=libs,
                    coverage=True,
                )
            )

    scenarios.append(
        Scenario(
            name="normal-autoload-all-installed",
            plugins=("env", "mock", "randomly", "sugar", "teamcity"),
            autoload=True,
        )
    )
    scenarios.append(
        Scenario(
            name="coverage-normal-autoload-all-installed",
            plugins=("env", "mock", "randomly", "sugar", "teamcity"),
            coverage=True,
            autoload=True,
        )
    )
    return scenarios


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="/tmp/retrace-design-partner-matrix")
    parser.add_argument("--repeat-failures", type=int, default=0)
    parser.add_argument("--keep-passing", action="store_true")
    parser.add_argument("--name-contains", default="")
    args = parser.parse_args()

    root = Path(args.root)
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    results = []
    scenarios = [
        scenario
        for scenario in build_scenarios()
        if args.name_contains in scenario.name
    ]
    for index, scenario in enumerate(scenarios, 1):
        scenario_root = root / f"{index:03d}-{scenario.name}"
        scenario_root.mkdir(parents=True)
        result = _record_extract_replay(scenario_root, scenario)
        item = {
            "name": scenario.name,
            "plugins": scenario.plugins,
            "libs": scenario.libs,
            "coverage": scenario.coverage,
            "autoload": scenario.autoload,
            **result,
        }
        results.append(item)
        status = "PASS" if item["ok"] else "FAIL"
        print(
            f"{index:03d}/{len(scenarios):03d} {status} "
            f"{scenario.name} [{item['stage']}] {item['signature']}"
        )
        if item["ok"] and not args.keep_passing:
            shutil.rmtree(scenario_root)

    if args.repeat_failures:
        failing = [item for item in results if not item["ok"]]
        for item in failing:
            scenario = Scenario(
                name=item["name"],
                plugins=tuple(item["plugins"]),
                libs=tuple(item["libs"]),
                coverage=bool(item["coverage"]),
                autoload=bool(item["autoload"]),
            )
            for repeat in range(1, args.repeat_failures + 1):
                repeat_root = root / f"repeat-{repeat}-{scenario.name}"
                repeat_root.mkdir(parents=True)
                result = _record_extract_replay(repeat_root, scenario)
                repeat_item = {
                    "name": f"{scenario.name}#repeat{repeat}",
                    "plugins": scenario.plugins,
                    "libs": scenario.libs,
                    "coverage": scenario.coverage,
                    "autoload": scenario.autoload,
                    **result,
                }
                results.append(repeat_item)
                status = "PASS" if repeat_item["ok"] else "FAIL"
                print(
                    f"REP {status} {repeat_item['name']} "
                    f"[{repeat_item['stage']}] {repeat_item['signature']}"
                )
                if repeat_item["ok"] and not args.keep_passing:
                    shutil.rmtree(repeat_root)

    summary = {
        "total": len(results),
        "passed": sum(1 for item in results if item["ok"]),
        "failed": sum(1 for item in results if not item["ok"]),
        "failures": [
            {
                "name": item["name"],
                "plugins": item["plugins"],
                "libs": item["libs"],
                "coverage": item["coverage"],
                "autoload": item["autoload"],
                "stage": item["stage"],
                "signature": item["signature"],
                "root": item["root"],
            }
            for item in results
            if not item["ok"]
        ],
    }
    print("JSON_SUMMARY_START")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print("JSON_SUMMARY_END")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
