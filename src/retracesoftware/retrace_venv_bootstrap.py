from __future__ import annotations

import os
import sys
from pathlib import Path

from retracesoftware.retracepython import (
    VENV_BOOTSTRAP_DISABLE_ENV,
    auto_debug_enabled,
    is_multiprocessing_bootstrap,
)


_TRUTHY = {"1", "true", "yes", "on"}


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in _TRUTHY


def activation_requested(env: dict[str, str] | os._Environ[str] = os.environ) -> bool:
    return (
        _truthy(env.get("RETRACE"))
        or auto_debug_enabled(env)
        or "RETRACE_RECORDING" in env
        or "RETRACE_CONFIG" in env
    )


def _is_retrace_module(name: str) -> bool:
    return name == "retracesoftware" or name.startswith("retracesoftware.")


def _is_multiprocessing_bootstrap(args: list[str]) -> bool:
    return is_multiprocessing_bootstrap(args)


def should_bypass(args: list[str]) -> bool:
    if not args:
        return True

    if _is_multiprocessing_bootstrap(args):
        return True

    if args[0] == "-m" and len(args) >= 2:
        module = args[1]
        return (
            module in {"pip", "ensurepip", "venv", "virtualenv"}
            or _is_retrace_module(module)
        )

    command = Path(args[0]).name
    if command in {"pip", "easy_install", "retrace", "replay", "retracepython", "retrace-venv"}:
        return True
    if command.startswith("pip") or command.startswith("easy_install-"):
        return True

    return False


def _orig_argv() -> list[str]:
    return list(getattr(sys, "orig_argv", sys.argv))


def bootstrap() -> None:
    if _truthy(os.environ.get(VENV_BOOTSTRAP_DISABLE_ENV)):
        return
    if not activation_requested():
        return

    target_argv = _orig_argv()[1:]
    if should_bypass(target_argv):
        return

    from retracesoftware.retracepython import build_retrace_command, run_with_auto_debug

    executable, command, env = build_retrace_command(
        target_argv,
        propagate_to_child_pythons=True,
    )
    if auto_debug_enabled():
        rc = run_with_auto_debug(
            executable,
            command,
            env,
            target_argv,
            cleanup_recording_on_success="RETRACE_RECORDING" not in os.environ,
        )
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(rc)
    os.execve(executable, command, env)


bootstrap()
