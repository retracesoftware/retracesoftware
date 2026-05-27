from __future__ import annotations

import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parent
BUNDLE = ROOT / "recordings" / "pytest-failed-run"
TRACE = BUNDLE / "trace.retrace"
PYTEST_XML = BUNDLE / "pytest.xml"
STDOUT_LOG = BUNDLE / "stdout.log"
PIP_FREEZE = BUNDLE / "pip-freeze.txt"
MANIFEST = BUNDLE / "retrace-manifest.json"
REPLAY_MD = BUNDLE / "replay.md"


def metadata_version(package: str) -> str | None:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return None


def run_text(command: Iterable[str]) -> str | None:
    try:
        result = subprocess.run(
            list(command),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def clean_previous_bundle() -> None:
    BUNDLE.mkdir(parents=True, exist_ok=True)
    for path in (TRACE, PYTEST_XML, STDOUT_LOG, PIP_FREEZE, MANIFEST, REPLAY_MD):
        path.unlink(missing_ok=True)
    shutil.rmtree(BUNDLE / "trace.d", ignore_errors=True)


def write_pip_freeze() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pip", "freeze"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    PIP_FREEZE.write_text(result.stdout, encoding="utf-8")


def write_manifest(
    pytest_args: list[str],
    record_command: list[str],
    exit_code: int,
) -> None:
    manifest = {
        "schema": "retracesoftware.pytest-manifest.v0",
        "ci": {
            "provider": "github-actions" if os.environ.get("GITHUB_ACTIONS") else "local",
            "run_id": os.environ.get("GITHUB_RUN_ID"),
            "job": os.environ.get("GITHUB_JOB"),
            "matrix": None,
        },
        "git": {
            "repository": run_text(["git", "config", "--get", "remote.origin.url"]),
            "sha": run_text(["git", "rev-parse", "HEAD"]),
            "ref": run_text(["git", "branch", "--show-current"]),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "tools": {
            "retrace": metadata_version("retracesoftware"),
            "pytest": metadata_version("pytest"),
        },
        "pytest": {
            "command": ["python", "-m", "pytest", *pytest_args],
            "exit_code": exit_code,
            "plugin_autoload_disabled": True,
            "junit_xml": str(PYTEST_XML.relative_to(ROOT)),
        },
        "recording": {
            "record_command": record_command,
            "trace": str(TRACE.relative_to(ROOT)),
            "working_directory": str(ROOT),
            "source_root": str(ROOT),
        },
        "notes": [
            "Environment variables are intentionally not captured by this manifest.",
            "This quickstart disables pytest plugin autoload to keep the preview deterministic.",
        ],
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def write_replay_instructions() -> None:
    REPLAY_MD.write_text(
        f"""# Replay This Failed pytest Run

From the quickstart directory:

```bash
cd {ROOT}
source .venv/bin/activate
./recordings/pytest-failed-run/trace.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording recordings/pytest-failed-run/trace.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"
./recordings/pytest-failed-run/trace.d/${{ROOT_PID}}.bin
```

For VS Code:

1. Open this folder with `code {ROOT}`.
2. Install the Retrace Debug Extension.
3. Open `recordings/pytest-failed-run/trace.retrace` from the Retrace sidebar.
4. Open `pytest_demo/checkout.py`.
5. Set a breakpoint inside `build_receipt`.
6. Start replay from the Retrace view.

For an AI agent:

Ask it to read `stdout.log`, `pytest.xml`, and the source code, then run the
terminal replay above before proposing a fix. The replay is the recorded failed
execution, not a new live pytest run.
""",
        encoding="utf-8",
    )


def main() -> int:
    clean_previous_bundle()

    pytest_args = sys.argv[1:] or ["pytest_demo", "-q", "--tb=short"]
    if pytest_args and pytest_args[0] == "--":
        pytest_args = pytest_args[1:]
    pytest_args = [*pytest_args, f"--junitxml={PYTEST_XML}"]

    record_command = [
        sys.executable,
        "-m",
        "retracesoftware",
        "--recording",
        str(TRACE),
        "--",
        "-m",
        "pytest",
        *pytest_args,
    ]

    env = os.environ.copy()
    env.pop("RETRACE_RECORDING", None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONUNBUFFERED"] = "1"

    print("Running pytest under Retrace...")
    print(" ".join(record_command))
    print()

    with STDOUT_LOG.open("w", encoding="utf-8") as stdout_log:
        process = subprocess.Popen(
            record_command,
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
            stdout_log.write(line)
        exit_code = process.wait()

    if exit_code == 0:
        TRACE.unlink(missing_ok=True)
        print("\npytest passed; discarded the recording because there is no failure to replay.")
        return 0

    write_pip_freeze()
    write_manifest(pytest_args, record_command, exit_code)
    write_replay_instructions()

    print(f"\npytest failed with exit code {exit_code}.")
    print(f"Replay bundle written to: {BUNDLE}")
    print("Next: read recordings/pytest-failed-run/replay.md")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
