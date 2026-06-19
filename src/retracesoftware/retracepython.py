from __future__ import annotations

import os
import shlex
import subprocess
import sys
from pathlib import Path


AUTO_DEBUG_ENV = "RETRACE_AUTO_DEBUG"
AUTO_DEBUG_SUPERVISED_ENV = "RETRACE_AUTO_DEBUG_SUPERVISED"
AI_SERVER_ENV = "RETRACE_AI_SERVER"
DEFAULT_AI_SERVER = "https://retrace-ai-service.retracesoftware.workers.dev"
RECORDING_INODE_ENV = "RETRACE_RECORDING_INODE"
VENV_BOOTSTRAP_DISABLE_ENV = "RETRACE_NO_VENV_BOOTSTRAP"
_TRUTHY = {"1", "true"}


def _usage() -> str:
    return """usage: retracepython [retrace options] <script.py | -m module | -c code> [args...]

Retrace options:
  --recording PATH          Recording path (default from RETRACE_CONFIG/release)
  --config NAME_OR_PATH     Config preset or TOML path (default: RETRACE_CONFIG/release)
  --verbose                 Enable verbose recording output
  --stacktraces             Capture stack traces for recorded events
  -h, --help                Show this help
"""


def _parse_args(argv: list[str]) -> tuple[dict[str, object], list[str]]:
    options: dict[str, object] = {
        "recording": None,
        "config": None,
        "verbose": False,
        "stacktraces": False,
    }

    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            return options, argv[i + 1 :]
        if arg in ("-h", "--help"):
            options["help"] = True
            return options, []
        if arg in ("--recording", "--retrace-recording"):
            if i + 1 >= len(argv):
                raise SystemExit(f"{arg} requires a value")
            options["recording"] = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--recording="):
            options["recording"] = arg.split("=", 1)[1]
            i += 1
            continue
        if arg.startswith("--retrace-recording="):
            options["recording"] = arg.split("=", 1)[1]
            i += 1
            continue
        if arg in ("--config", "--retrace-config"):
            if i + 1 >= len(argv):
                raise SystemExit(f"{arg} requires a value")
            options["config"] = argv[i + 1]
            i += 2
            continue
        if arg.startswith("--config="):
            options["config"] = arg.split("=", 1)[1]
            i += 1
            continue
        if arg.startswith("--retrace-config="):
            options["config"] = arg.split("=", 1)[1]
            i += 1
            continue
        if arg in ("--verbose", "--retrace-verbose"):
            options["verbose"] = True
            i += 1
            continue
        if arg in ("--stacktraces", "--retrace-stacktraces"):
            options["stacktraces"] = True
            i += 1
            continue
        return options, argv[i:]

    return options, []


def _script_stem(argv: list[str]) -> str:
    if not argv:
        return "recording"
    if argv[0] == "-m" and len(argv) >= 2:
        return argv[1]
    if argv[0] == "-c":
        return "command"
    for arg in argv:
        if not arg.startswith("-"):
            return Path(arg).stem
    return "recording"


def is_multiprocessing_bootstrap(argv: list[str]) -> bool:
    return (
        "--multiprocessing-fork" in argv
        or any(
            (
                "multiprocessing.spawn" in arg
                and "spawn_main" in arg
            )
            or (
                "multiprocessing.resource_tracker" in arg
                and "main" in arg
            )
            for arg in argv
        )
    )


def auto_debug_enabled(env: dict[str, str] | os._Environ[str] = os.environ) -> bool:
    if env.get(AUTO_DEBUG_SUPERVISED_ENV, "").strip().lower() in _TRUTHY:
        return False
    return env.get(AUTO_DEBUG_ENV, "").strip().lower() in _TRUTHY


def _exec_untraced_python(target_argv: list[str]) -> None:
    real_python = os.environ.get("RETRACE_REAL_PYTHON") or sys.executable
    os.execve(real_python, [real_python, *target_argv], os.environ.copy())


def _trace_identity(path: str | os.PathLike[str]) -> str:
    st = os.stat(path)
    return f"{st.st_dev}:{st.st_ino}"


def _prepare_trace_file(path: str) -> None:
    try:
        existing_identity = _trace_identity(path)
    except FileNotFoundError:
        existing_identity = None

    if (
        existing_identity is not None
        and os.environ.get(RECORDING_INODE_ENV) == existing_identity
    ):
        return

    from retracesoftware.replay import extract_binary_path

    extract_bin = extract_binary_path()
    shebang = f"#!{extract_bin} --recording\n"
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "wb") as f:
        f.write(shebang.encode("utf-8"))
    os.chmod(path, 0o755)
    os.environ[RECORDING_INODE_ENV] = _trace_identity(path)


def _resolve_recording_path(recording: str, target_argv: list[str]) -> str:
    from retracesoftware.tape import expand_recording_path

    if "{script}" in recording:
        recording = recording.replace("{script}", _script_stem(target_argv))
    return expand_recording_path(recording)


def build_retrace_command(
    target_argv: list[str],
    *,
    config_name: str | None = None,
    recording: str | None = None,
    verbose: bool = False,
    stacktraces: bool = False,
    propagate_to_child_pythons: bool = False,
) -> tuple[str, list[str], dict[str, str]]:
    from retracesoftware.install.config import config_to_argv, load_retrace_config

    config = load_retrace_config(config_name)
    record_config = config.setdefault("record", {})
    if recording is not None:
        record_config["recording"] = recording
    if verbose:
        record_config["verbose"] = True
    if stacktraces:
        record_config["stacktraces"] = True

    resolved_recording = record_config.get("recording")
    if resolved_recording is not None:
        resolved_recording = _resolve_recording_path(str(resolved_recording), target_argv)
        record_config["recording"] = resolved_recording
        if resolved_recording != "disable":
            _prepare_trace_file(resolved_recording)

    real_python = os.environ.get("RETRACE_REAL_PYTHON") or sys.executable
    env = os.environ.copy()
    if resolved_recording is not None:
        env["RETRACE_RECORDING"] = str(resolved_recording)
    if config_name is not None:
        env["RETRACE_CONFIG"] = config_name

    wrapper = os.environ.get("RETRACE_PYTHON_WRAPPER")
    if wrapper:
        env["PYTHONEXECUTABLE"] = wrapper
    if propagate_to_child_pythons:
        env.pop(VENV_BOOTSTRAP_DISABLE_ENV, None)
    else:
        env[VENV_BOOTSTRAP_DISABLE_ENV] = "1"

    argv = [real_python, "-m", "retracesoftware"]
    argv.extend(config_to_argv(config))
    argv.append("--")
    argv.extend(target_argv)
    return real_python, argv, env


def run_with_auto_debug(
    executable: str,
    command: list[str],
    env: dict[str, str],
    target_argv: list[str] | None = None,
    *,
    cleanup_recording_on_success: bool = False,
) -> int:
    recording = env.get("RETRACE_RECORDING")
    child_env = env.copy()
    child_env[AUTO_DEBUG_SUPERVISED_ENV] = "1"

    completed = subprocess.run(command, env=child_env)
    if completed.returncode == 0:
        if cleanup_recording_on_success and recording and recording != "disable":
            _delete_recording(recording)
        return completed.returncode
    if not recording or recording == "disable":
        return completed.returncode

    if Path(recording).exists():
        run_ai_debugger(
            recording=recording,
            target_exit=completed.returncode,
            target_argv=target_argv or command,
        )
    else:
        print(
            f"Retrace auto-debug: target exited with code {completed.returncode}, "
            f"but recording was not found: {recording}",
            file=sys.stderr,
        )
    return completed.returncode


def run_ai_debugger(
    *,
    recording: str,
    target_exit: int,
    target_argv: list[str],
) -> int:
    recording_path = Path(recording).resolve()
    report_out = _env_path("RETRACE_AI_REPORT_OUT") or recording_path.with_suffix(".ai-report.json")
    report_md = _env_path("RETRACE_AI_REPORT_MD") or recording_path.with_suffix(".ai-report.md")
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_md.parent.mkdir(parents=True, exist_ok=True)

    command = build_ai_driver_command(
        recording=recording_path,
        report_out=report_out,
        report_md=report_md,
        target_exit=target_exit,
        target_argv=target_argv,
    )
    env = ai_debugger_env()

    print(
        f"Retrace auto-debug: target exited with code {target_exit}; "
        f"running AI debugger for {recording_path}",
        file=sys.stderr,
    )

    try:
        completed = subprocess.run(command, env=env)
    except FileNotFoundError:
        print(
            f"Retrace auto-debug: AI driver executable not found: {command[0]}",
            file=sys.stderr,
        )
        return 127
    except PermissionError:
        print(
            f"Retrace auto-debug: AI driver executable is not runnable: {command[0]}",
            file=sys.stderr,
        )
        return 126

    if report_md.exists():
        print(f"Retrace auto-debug: AI report written to {report_md}", file=sys.stderr)
    if completed.returncode != 0:
        print(
            f"Retrace auto-debug: AI debugger exited with {_normalize_exit_code(completed.returncode)}; "
            f"preserving original target exit {_normalize_exit_code(target_exit)}",
            file=sys.stderr,
        )
    return _normalize_exit_code(completed.returncode)


def build_ai_driver_command(
    *,
    recording: Path,
    report_out: Path,
    report_md: Path,
    target_exit: int,
    target_argv: list[str],
) -> list[str]:
    command = _ai_driver_base_command()
    command.extend(
        [
            "--tool-executor",
            os.environ.get("RETRACE_TOOL_EXECUTOR", "dap"),
            "--trace",
            str(recording),
            "--target",
            os.environ.get("RETRACE_AI_TARGET", "target_application"),
            "--report-out",
            str(report_out),
            "--report-md",
            str(report_md),
            "--task",
            os.environ.get("RETRACE_AI_TASK")
            or _default_ai_task(recording, target_exit, target_argv),
        ]
    )

    optional_flags = (
        ("--replay-bin", os.environ.get("RETRACE_REPLAY_BIN")),
        ("--max-tool-calls", os.environ.get("RETRACE_AI_MAX_TOOL_CALLS")),
        ("--time-budget", os.environ.get("RETRACE_AI_TIME_BUDGET")),
        ("--max-output-tokens", os.environ.get("RETRACE_AI_MAX_OUTPUT_TOKENS")),
    )
    for flag, value in optional_flags:
        if value:
            command.extend([flag, value])
    return command


def _ai_driver_base_command() -> list[str]:
    driver_command = os.environ.get("RETRACE_AI_DRIVER_COMMAND")
    if driver_command:
        command = shlex.split(driver_command)
    elif os.environ.get("RETRACE_AI_DRIVER"):
        command = [os.environ["RETRACE_AI_DRIVER"]]
    else:
        command = [sys.executable, "-m", "retracesoftware.ai_driver"]
    if not command:
        raise ValueError("AI driver command is empty")
    return command


def _default_ai_task(recording: Path, target_exit: int, target_argv: list[str]) -> str:
    return (
        "The target command exited non-zero while running under Retrace recording. "
        f"Use the deterministic replay at {recording} through the DAP tools to diagnose "
        f"the target application failure. Target exit code: {_normalize_exit_code(target_exit)}. "
        f"Target command: {_format_command(target_argv)}."
    )


def _format_command(command: list[str]) -> str:
    return shlex.join(command)


def ai_debugger_env() -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "RETRACE",
        "RETRACE_RECORDING",
        "RETRACE_CONFIG",
        "RETRACE_INODE",
        AUTO_DEBUG_ENV,
        RECORDING_INODE_ENV,
        VENV_BOOTSTRAP_DISABLE_ENV,
        AUTO_DEBUG_SUPERVISED_ENV,
    ):
        env.pop(key, None)
    if not env.get(AI_SERVER_ENV):
        env[AI_SERVER_ENV] = DEFAULT_AI_SERVER
    env["RETRACEPYTHON_BYPASS"] = "1"
    return env


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    if not value:
        return None
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return Path.cwd() / path


def _normalize_exit_code(returncode: int) -> int:
    if returncode < 0:
        return 128 + abs(returncode)
    return returncode


def _delete_recording(recording: str) -> None:
    try:
        Path(recording).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        print(
            f"Retrace auto-debug: could not delete successful recording {recording}: {exc}",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    options, target_argv = _parse_args(argv)
    if options.get("help"):
        print(_usage(), end="")
        return 0
    if not target_argv:
        print(_usage(), file=sys.stderr, end="")
        return 2
    if os.environ.get("RETRACE_PYTHON_WRAPPER") and is_multiprocessing_bootstrap(target_argv):
        _exec_untraced_python(target_argv)

    explicit_recording = (
        options["recording"] is not None
        or "RETRACE_RECORDING" in os.environ
    )
    executable, command, env = build_retrace_command(
        target_argv,
        config_name=options["config"],  # type: ignore[arg-type]
        recording=options["recording"],  # type: ignore[arg-type]
        verbose=bool(options["verbose"]),
        stacktraces=bool(options["stacktraces"]),
        propagate_to_child_pythons=bool(os.environ.get("RETRACE_PYTHON_WRAPPER")),
    )
    if auto_debug_enabled():
        return run_with_auto_debug(
            executable,
            command,
            env,
            target_argv,
            cleanup_recording_on_success=not explicit_recording,
        )
    os.execve(executable, command, env)
    raise AssertionError("os.execve returned")


if __name__ == "__main__":
    raise SystemExit(main())
