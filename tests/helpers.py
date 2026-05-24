"""Shared test helpers for retracesoftware integration tests."""
import os
import sys
import subprocess
import shutil
from pathlib import Path

TIMEOUT = 30
_REPO_ROOT = Path(__file__).resolve().parents[1]
_PYTHON_TAGS = {}
_PYTEST_REEXECED = "RETRACE_PYTEST_REEXECED"
_RETRACE_RUNTIME_NAMES = (
    "call_at",
    "CoordinateSpace",
    "coordinates",
    "disabled_space",
    "space_dispatch",
    "thread_delta",
)


def is_retrace_python():
    try:
        import retrace
    except ImportError:
        return False
    return all(hasattr(retrace, name) for name in _RETRACE_RUNTIME_NAMES)


def is_retrace_python_executable(python):
    proc = subprocess.run(
        [
            os.fspath(python),
            "-c",
            (
                "import retrace, sys\n"
                f"names = {tuple(_RETRACE_RUNTIME_NAMES)!r}\n"
                "missing = [name for name in names if not hasattr(retrace, name)]\n"
                "raise SystemExit(1 if missing else 0)\n"
            ),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=10,
    )
    return proc.returncode == 0


def packaged_retrace_python():
    try:
        from retracesoftware_cpython import executable
    except ImportError:
        return None

    path = Path(executable())
    return str(path) if path.is_file() else None


def local_retrace_python():
    root = Path(__file__).resolve().parents[2] / "retrace-cpython"
    for path in sorted(
        root.glob(".venv-*/lib/python*/site-packages/retracesoftware_cpython/_runtime/bin/python"),
        reverse=True,
    ):
        if path.is_file():
            return str(path)
    for path in sorted(root.glob(".venv-*/bin/retrace-python"), reverse=True):
        if path.is_file():
            return str(path)
    path = root / ".venv" / "bin" / "retrace-python"
    if path.is_file():
        return str(path)
    return None


def local_retrace_python_builds():
    root = Path(__file__).resolve().parents[2] / "retrace-cpython"
    return [
        str(path)
        for path in sorted(root.glob("build/install/*/bin/python3.12"), reverse=True)
        if path.is_file()
    ]


def retrace_python():
    python = os.environ.get("RETRACE_PYTHON")
    if python:
        if not is_retrace_python_executable(python):
            raise RuntimeError(
                "RETRACE_PYTHON does not satisfy current retrace-python runtime contract"
            )
        return python

    if is_retrace_python():
        return sys.executable

    for python in (
        packaged_retrace_python(),
        local_retrace_python(),
        shutil.which("retrace-python"),
        *local_retrace_python_builds(),
    ):
        if python and is_retrace_python_executable(python):
            return python

    raise RuntimeError("current retrace-python not found; set RETRACE_PYTHON")


def ensure_pytest_runs_under_retrace_python():
    if is_retrace_python():
        return
    if os.environ.get(_PYTEST_REEXECED):
        raise RuntimeError("pytest re-execed under retrace-python but retrace is still unavailable")

    python = retrace_python()
    env = retrace_env(os.environ, python)
    env[_PYTEST_REEXECED] = "1"
    os.execvpe(os.fspath(python), [os.fspath(python), "-m", "pytest", *sys.argv[1:]], env)


def _python_tag(python):
    python = os.fspath(python)
    tag = _PYTHON_TAGS.get(python)
    if tag is not None:
        return tag

    proc = subprocess.run(
        [
            python,
            "-c",
            "import sys; print(f'cp{sys.version_info.major}{sys.version_info.minor}{getattr(sys, \"abiflags\", \"\")}')",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"failed to inspect retrace-python version:\n{proc.stderr}")
    tag = proc.stdout.strip()
    _PYTHON_TAGS[python] = tag
    return tag


def _dedupe(paths):
    result = []
    for path in paths:
        if path and path not in result:
            result.append(path)
    return result


def _site_package_paths():
    return [
        path for path in sys.path
        if path and ("site-packages" in path or "dist-packages" in path)
    ]


def _local_build_paths(python):
    build = _REPO_ROOT / "build" / _python_tag(python)
    return [
        str(build / "cpp" / name)
        for name in ("functional", "utils", "stream", "cursor")
        if (build / "cpp" / name).exists()
    ]


def _without_other_builds(paths, python):
    current_build = str(_REPO_ROOT / "build" / _python_tag(python))
    return [
        path for path in paths
        if f"{os.sep}build{os.sep}cp" not in path or path.startswith(current_build)
    ]


def _mesonpy_skip_paths():
    paths = []
    for finder in sys.meta_path:
        if finder.__class__.__name__ == "MesonpyMetaFinder":
            path = getattr(finder, "_build_path", None)
            if path:
                paths.append(path)
    return paths


def retrace_env(env=None, python=None):
    python = os.fspath(python or retrace_python())
    run_env = os.environ.copy()
    if env:
        run_env.update(env)

    current_pythonpath = run_env.get("PYTHONPATH", "").split(os.pathsep)
    pythonpath = _dedupe([
        str(_REPO_ROOT / "src"),
        str(_REPO_ROOT),
        *_local_build_paths(python),
        *_without_other_builds(current_pythonpath, python),
        *_site_package_paths(),
    ])
    run_env["PYTHONPATH"] = os.pathsep.join(pythonpath)

    skip = _dedupe([
        *run_env.get("MESONPY_EDITABLE_SKIP", "").split(os.pathsep),
        *_mesonpy_skip_paths(),
    ])
    if skip:
        run_env["MESONPY_EDITABLE_SKIP"] = os.pathsep.join(skip)
    return run_env


class _RetracePython(os.PathLike):
    def __fspath__(self):
        return retrace_python()

    def __str__(self):
        return retrace_python()

    def __repr__(self):
        return repr(retrace_python())


PYTHON = _RetracePython()


def run_record(script_path, recording, extra_args=None, env=None, stacktraces=True):
    """Run a script under retrace recording.

    *recording* is a trace file path (e.g. ``/tmp/dir/trace.retrace``).
    Returns the CompletedProcess.
    """
    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", recording,
        "--format", "unframed_binary",
    ]
    if stacktraces:
        cmd.append("--stacktraces")
    cmd.extend(["--", str(script_path)])
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=TIMEOUT, env=retrace_env(env, PYTHON),
    )


def run_replay(recording, extra_args=None, env=None):
    """Replay from a trace file.

    *recording* is a trace file path (e.g. ``/tmp/dir/trace.retrace``).
    Returns the CompletedProcess.
    """
    cmd = [
        PYTHON, "-m", "retracesoftware",
        "--recording", recording,
    ]
    if extra_args:
        cmd.extend(extra_args)

    return subprocess.run(
        cmd, capture_output=True, text=True,
        timeout=TIMEOUT, env=retrace_env(env, PYTHON),
    )
