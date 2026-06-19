from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import shlex
import stat
import subprocess
import sys
import sysconfig
import venv


_EXTENSION_MODULES = (
    "_retracesoftware_utils_release",
    "_retracesoftware_utils_debug",
    "_retracesoftware_functional_release",
    "_retracesoftware_functional_debug",
    "_retracesoftware_stream_release",
    "_retracesoftware_stream_debug",
    "_retracesoftware_cursor_release",
    "_retracesoftware_cursor_debug",
)

CURRENT_HOOK_PTH = "retracesoftware_hook.pth"
CURRENT_HOOK_PATHS_PTH = "retracesoftware_00_paths.pth"
LEGACY_CURRENT_VENV_PTH = "retracesoftware_venv.pth"
LEGACY_AUTOENABLE_PTH = "retracesoftware_autoenable.pth"


def activation_pth_source() -> str:
    return (
        "import os; "
        "(os.environ.get('RETRACE', '').strip().lower() in "
        "('1', 'true', 'yes', 'on') "
        "or os.environ.get('RETRACE_AUTO_DEBUG', '').strip().lower() in "
        "('1', 'true') "
        "or 'RETRACE_RECORDING' in os.environ "
        "or 'RETRACE_CONFIG' in os.environ) "
        "and __import__('retracesoftware.retrace_venv_bootstrap')\n"
    )


def current_hook_pth_target() -> Path:
    return Path(sysconfig.get_paths()["purelib"]) / CURRENT_HOOK_PTH


def current_hook_paths_pth_target() -> Path:
    return Path(sysconfig.get_paths()["purelib"]) / CURRENT_HOOK_PATHS_PTH


def legacy_autoenable_pth_target() -> Path:
    return Path(sysconfig.get_paths()["purelib"]) / LEGACY_AUTOENABLE_PTH


def _clear_hidden_flag(path: Path) -> None:
    if hasattr(os, "chflags") and hasattr(stat, "UF_HIDDEN"):
        try:
            flags = os.stat(path).st_flags
            if flags & stat.UF_HIDDEN:
                os.chflags(path, flags & ~stat.UF_HIDDEN)
        except OSError:
            pass


def _path_link_source() -> str:
    return "".join(f"{path}\n" for path in _current_retrace_paths())


def enable_current_hook(
    target: Path | None = None,
    paths_target: Path | None = None,
) -> tuple[Path, Path]:
    if target is None:
        target = current_hook_pth_target()
        if paths_target is None:
            paths_target = current_hook_paths_pth_target()
    elif paths_target is None:
        paths_target = target.with_name(CURRENT_HOOK_PATHS_PTH)
    paths_target.write_text(_path_link_source(), encoding="utf-8")
    _clear_hidden_flag(paths_target)
    target.write_text(activation_pth_source(), encoding="utf-8")
    _clear_hidden_flag(target)
    return target, paths_target


def disable_current_hook(
    target: Path | None = None,
    paths_target: Path | None = None,
) -> list[Path]:
    if target is None:
        target = current_hook_pth_target()
        if paths_target is None:
            paths_target = current_hook_paths_pth_target()
    elif paths_target is None:
        paths_target = target.with_name(CURRENT_HOOK_PATHS_PTH)

    removed: list[Path] = []
    for candidate in (
        target,
        paths_target,
        target.with_name(LEGACY_CURRENT_VENV_PTH),
    ):
        if candidate.exists():
            candidate.unlink()
            removed.append(candidate)
    return removed


def remove_legacy_autoenable(target: Path | None = None) -> bool:
    if target is None:
        target = legacy_autoenable_pth_target()
    if target.exists():
        target.unlink()
        return True
    return False


def _current_retrace_paths() -> list[Path]:
    import retracesoftware

    paths: list[Path] = []
    for package_path in getattr(retracesoftware, "__path__", []):
        paths.append(Path(package_path).resolve().parent)

    for name in _EXTENSION_MODULES:
        spec = importlib.util.find_spec(name)
        origin = getattr(spec, "origin", None) if spec is not None else None
        if origin:
            paths.append(Path(origin).resolve().parent)

    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique


def _purelib(real_python: Path) -> Path:
    script = "import sysconfig; print(sysconfig.get_paths()['purelib'])"
    proc = subprocess.run(
        [str(real_python), "-c", script],
        check=True,
        capture_output=True,
        text=True,
    )
    return Path(proc.stdout.strip())


def _write_path_link(real_python: Path) -> None:
    purelib = _purelib(real_python)
    purelib.mkdir(parents=True, exist_ok=True)
    link = purelib / "retracesoftware-current.pth"
    link.write_text(_path_link_source(), encoding="utf-8")


def _quote(value: str | os.PathLike[str]) -> str:
    return shlex.quote(os.fspath(value))


def _wrapper_script(real_python: Path) -> str:
    real = _quote(real_python)
    return f"""#!/bin/sh
REAL={real}
WRAPPER="$0"

case "${{RETRACEPYTHON_BYPASS:-}}" in
  1|true|TRUE|yes|YES|on|ON)
    unset PYTHONEXECUTABLE
    exec "$REAL" "$@"
    ;;
esac

if [ "$#" -eq 0 ]; then
  unset PYTHONEXECUTABLE
  exec "$REAL" "$@"
fi

if [ "$1" = "-m" ]; then
  case "${{2:-}}" in
    pip|ensurepip|venv|virtualenv|retracesoftware|retracesoftware.*)
      unset PYTHONEXECUTABLE
      exec "$REAL" "$@"
      ;;
  esac
fi

case "${{1:-}}" in
  -*)
    first_basename="${{1:-}}"
    ;;
  *)
    first_basename="$(basename "${{1:-}}")"
    ;;
esac

case "$first_basename" in
  pip|pip[0-9]*|easy_install|easy_install-*)
    unset PYTHONEXECUTABLE
    exec "$REAL" "$@"
    ;;
esac

export RETRACE_REAL_PYTHON="$REAL"
export RETRACE_PYTHON_WRAPPER="$WRAPPER"
export PYTHONEXECUTABLE="$WRAPPER"
exec "$REAL" -m retracesoftware.retracepython "$@"
"""


def _replace_python_launchers(venv_dir: Path) -> None:
    if os.name != "posix":
        raise SystemExit("retrace-venv currently supports POSIX virtualenv layouts")

    bin_dir = venv_dir / "bin"
    python = bin_dir / "python"
    if not python.exists():
        raise FileNotFoundError(f"missing venv Python executable: {python}")

    real_python = bin_dir / ".retrace-python-real"
    if not real_python.exists():
        if python.is_symlink():
            target = os.readlink(python)
            target_path = Path(target)
            if not target_path.is_absolute():
                target_path = (python.parent / target_path).resolve()
            real_python.symlink_to(target_path)
            python.unlink()
        else:
            python.rename(real_python)

    _write_path_link(real_python)

    version = f"{sys.version_info.major}.{sys.version_info.minor}"
    for name in ("python", f"python{sys.version_info.major}", f"python{version}"):
        launcher = bin_dir / name
        if launcher.exists() or launcher.is_symlink():
            launcher.unlink()
        launcher.write_text(_wrapper_script(real_python), encoding="utf-8")
        mode = launcher.stat().st_mode
        launcher.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def create_retrace_venv(
    path: str | os.PathLike[str],
    *,
    clear: bool = False,
    with_pip: bool = True,
    system_site_packages: bool = False,
    prompt: str | None = None,
) -> Path:
    venv_dir = Path(path)
    builder = venv.EnvBuilder(
        clear=clear,
        system_site_packages=system_site_packages,
        with_pip=with_pip,
        symlinks=True,
        prompt=prompt,
    )
    builder.create(venv_dir)
    _replace_python_launchers(venv_dir)
    return venv_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="retrace-venv",
        description="Create a virtualenv whose python launcher records through Retrace.",
    )
    parser.add_argument("path", help="Virtualenv directory to create")
    parser.add_argument("--clear", action="store_true", help="Delete an existing environment before creating it")
    parser.add_argument("--without-pip", action="store_true", help="Do not install or upgrade pip in the venv")
    parser.add_argument(
        "--system-site-packages",
        action="store_true",
        help="Give the venv access to the current interpreter's site packages",
    )
    parser.add_argument("--prompt", default=None, help="Prompt prefix for the venv activation script")
    args = parser.parse_args(argv)

    venv_dir = create_retrace_venv(
        args.path,
        clear=args.clear,
        with_pip=not args.without_pip,
        system_site_packages=args.system_site_packages,
        prompt=args.prompt,
    )
    print(f"Retrace venv created: {venv_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
