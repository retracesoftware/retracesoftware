"""Regression coverage for local LLM model boundaries.

Application-level LLM demos showed that PidFile replay was still executing
HuggingFace cache lookup and llama_cpp model initialization live.
This test uses tiny fake ``huggingface_hub`` and ``llama_cpp`` packages with the
same public import shape. The fake model-boundary functions compare patched
``os.getpid()`` with a direct libc ``getpid()`` call; those match during record,
but differ during replay if Retrace live-enters the boundary body instead of
returning the recorded result.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from tests.helpers import run_record, run_replay


_ROOT = Path(__file__).resolve().parents[3]
_FLIGHT_SEARCH_QUERY = (
    "Book the cheapest flight from Barcelona to a US city on the East Coast "
    "that is not NYC on 2026-02-14"
)


def test_replay_does_not_live_run_huggingface_model_download_boundary(
    tmp_path: Path,
) -> None:
    _assert_model_boundary_replay(
        tmp_path,
        script_source="""
            from huggingface_hub import hf_hub_download


            model_path = hf_hub_download(
                repo_id="Retrace/fake-model",
                filename="fake-q8.gguf",
            )

            print(f"MODEL {model_path}", flush=True)
        """,
        expected_stdout="MODEL /fake-cache/Retrace--fake-model/fake-q8.gguf\n",
    )


def test_replay_does_not_live_run_llama_cpp_model_execution_boundary(
    tmp_path: Path,
) -> None:
    _assert_model_boundary_replay(
        tmp_path,
        script_source="""
            from llama_cpp import Llama


            model = Llama(
                model_path="/fake-cache/local/fake-q8.gguf",
                n_ctx=32768,
                verbose=False,
            )
            response = model.create_chat_completion(
                [{"role": "user", "content": "book cheapest flight"}],
                max_tokens=32,
            )

            print(response["choices"][0]["message"]["content"], flush=True)
        """,
        expected_stdout="cheapest-flight:LH1901\n",
    )


def test_replay_huggingface_download_after_rich_status_output_matches_record(
    tmp_path: Path,
) -> None:
    """Regression for the cookbook flight-search replay failure.

    The flight-search assistant prints a Rich "Downloading ..." status line,
    then calls ``hf_hub_download``.  Current replay reaches the same status
    line, then fails around the recorded download boundary instead of consuming
    the recorded result and continuing to the post-download status line.
    """

    pytest.importorskip("huggingface_hub")
    pytest.importorskip("rich.console")
    _ensure_small_huggingface_file_is_cached()

    script = tmp_path / "rich_huggingface_download_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            from huggingface_hub import hf_hub_download
            from rich.console import Console


            console = Console()
            console.print(
                "[cyan]Downloading[/cyan] [yellow]config.json[/yellow] "
                "from HuggingFace repository: [blue]gpt2[/blue]"
            )
            path = hf_hub_download(repo_id="gpt2", filename="config.json")
            console.print(f"[green]OK[/green] Model file is at: [dim]{path}[/dim]")
            print("MODEL", path.rsplit("/", 1)[-1], flush=True)
            """
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONPATH"] = _local_pythonpath(tmp_path)
    env["RETRACE_CONFIG"] = "debug"

    record = run_record(str(script), str(recording), env=env, stacktraces=False)
    assert record.returncode == 0, (
        "record failed for Rich + HuggingFace download reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert "Downloading config.json from HuggingFace repository: gpt2" in record.stdout
    assert "MODEL config.json" in record.stdout

    replay = _run_replay_with_diagnostics(recording, env=env, timeout=15)

    assert replay.returncode == 0, (
        "replay failed after re-entering the HuggingFace download path\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Flight-search replay can consume a bind marker after recording via "
        "the Retrace venv path with a relative RETRACE_RECORDING"
    ),
)
def test_flight_search_relative_retrace_venv_pidfile_replay_matches_record(
    tmp_path: Path,
) -> None:
    """Regression for the current cookbook flight-search replay failure.

    The failing manual flow records the real flight-search assistant through
    the ``python -m retracesoftware venv`` / ``RETRACE_RECORDING=...`` path,
    with ``RETRACE_RECORDING`` set to a relative path in the app directory.

    Direct ``python -m retracesoftware --recording ...`` recordings and
    absolute ``RETRACE_RECORDING=/tmp/...`` recordings can pass on the same
    build, so this test deliberately keeps the relative always-on venv shape.
    """

    app_dir_text = os.environ.get("RETRACE_FLIGHT_SEARCH_ASSISTANT_DIR")
    if not app_dir_text:
        pytest.skip(
            "set RETRACE_FLIGHT_SEARCH_ASSISTANT_DIR to the cookbook "
            "flight-search-assistant checkout to run this regression"
        )

    app_dir = Path(app_dir_text).expanduser().resolve()
    script = app_dir / "flight_search.py"
    model_py = app_dir / "model.py"
    if not script.exists() or not model_py.exists():
        pytest.skip(f"not a flight-search-assistant checkout: {app_dir}")

    pytest.importorskip("huggingface_hub")
    pytest.importorskip("llama_cpp")
    pytest.importorskip("rich.console")
    _ensure_flight_search_model_is_cached()

    venv_dir = app_dir / ".retrace-venv"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "venv",
            str(venv_dir),
            "--without-pip",
            "--system-site-packages",
        ],
        cwd=app_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert install.returncode == 0, (
        "failed to create Retrace venv\n"
        f"stdout:\n{install.stdout}\n"
        f"stderr:\n{install.stderr}"
    )
    retrace_python = venv_dir / "bin" / "python"

    recording_name = "flight-relative-retrace-venv.retrace"
    trace_dir = app_dir / "flight-relative-retrace-venv.d"
    recording = app_dir / recording_name
    if recording.exists():
        recording.unlink()
    if trace_dir.exists():
        import shutil

        shutil.rmtree(trace_dir)

    record_env = os.environ.copy()
    record_env.pop("RETRACE_CONFIG", None)
    record_env["PYTHONFAULTHANDLER"] = "1"
    record_env["RETRACE_RECORDING"] = recording_name

    record = subprocess.run(
        [
            str(retrace_python),
            "flight_search.py",
            "--query",
            _FLIGHT_SEARCH_QUERY,
        ],
        cwd=app_dir,
        env=record_env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert record.returncode == 0, (
        "flight-search record failed\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert "Booking Reference:" in record.stdout

    extract = subprocess.run(
        [str(recording), "--extract"],
        cwd=app_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert extract.returncode == 0, (
        "extract failed\n"
        f"stdout:\n{extract.stdout}\n"
        f"stderr:\n{extract.stderr}"
    )

    list_pids = subprocess.run(
        [
            sys.executable,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--list_pids",
        ],
        cwd=app_dir,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert list_pids.returncode == 0, (
        "list_pids failed\n"
        f"stdout:\n{list_pids.stdout}\n"
        f"stderr:\n{list_pids.stderr}"
    )

    root_pid = list_pids.stdout.splitlines()[0]
    pidfile = trace_dir / f"{root_pid}.bin"
    assert pidfile.exists(), pidfile

    replay_env = os.environ.copy()
    replay_env.pop("RETRACE_RECORDING", None)
    replay_env["PYTHONFAULTHANDLER"] = "1"
    replay = subprocess.run(
        [str(pidfile)],
        cwd=app_dir,
        env=replay_env,
        capture_output=True,
        text=True,
        timeout=90,
    )
    combined = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        "flight-search relative Retrace venv PidFile replay diverged\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert "bind marker returned when bind was expected" not in combined
    assert replay.stdout == record.stdout


def _assert_model_boundary_replay(
    tmp_path: Path,
    *,
    script_source: str,
    expected_stdout: str,
) -> None:
    _write_fake_model_packages(tmp_path)

    script = tmp_path / "model_boundary_repro.py"
    script.write_text(textwrap.dedent(script_source), encoding="utf-8")

    recording = tmp_path / "trace.retrace"
    modules_path = tmp_path / "no_user_modules"

    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["PYTHONPATH"] = _local_pythonpath(tmp_path)
    env["RETRACE_CONFIG"] = "debug"
    env["RETRACE_MODULES_PATH"] = str(modules_path)
    env["RETRACE_RECORDING"] = str(recording)

    record = run_record(str(script), str(recording), env=env, stacktraces=False)
    assert record.returncode == 0, (
        "record failed for model-boundary reproducer\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert record.stdout == expected_stdout

    replay_env = env.copy()
    replay_env.pop("RETRACE_RECORDING", None)
    replay = run_replay(str(recording), env=replay_env)

    assert replay.returncode == 0, (
        "replay live-ran HuggingFace or llama_cpp model boundary code\n"
        f"exit: {replay.returncode}\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert replay.stdout == record.stdout


def _ensure_small_huggingface_file_is_cached() -> None:
    """Warm a tiny public file so the regression does not download a large model."""

    script = textwrap.dedent(
        """
        from huggingface_hub import hf_hub_download

        hf_hub_download(repo_id="gpt2", filename="config.json")
        """
    )
    env = os.environ.copy()
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    try:
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            env=env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not cache tiny HuggingFace fixture: {exc}")


def _ensure_flight_search_model_is_cached() -> None:
    """Require the real flight-search GGUF to already be in the local HF cache."""

    script = textwrap.dedent(
        """
        from huggingface_hub import hf_hub_download

        hf_hub_download(
            repo_id="LiquidAI/LFM2.5-1.2B-Thinking-GGUF",
            filename="LFM2.5-1.2B-Thinking-Q8_0.gguf",
            local_files_only=True,
        )
        """
    )
    env = os.environ.copy()
    env["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    try:
        subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
            env=env,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"flight-search GGUF is not cached locally: {exc}")


def _run_replay_with_diagnostics(
    recording: Path,
    *,
    env: dict[str, str],
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "retracesoftware",
                "--recording",
                str(recording),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_timeout_output(exc.stdout)
        stderr = _decode_timeout_output(exc.stderr)
        pytest.fail(
            "replay timed out after re-entering the HuggingFace download path\n"
            f"stdout:\n{stdout}\n"
            f"stderr:\n{stderr}"
        )


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _local_pythonpath(extra: Path) -> str:
    build_tag = (
        f"cp{sys.version_info.major}{sys.version_info.minor}"
        f"{getattr(sys, 'abiflags', '')}"
    )
    entries = [str(extra), str((_ROOT / "src").resolve())]
    for rel in (
        f"build/{build_tag}/cpp/utils",
        f"build/{build_tag}/cpp/stream",
        f"build/{build_tag}/cpp/functional",
        f"build/{build_tag}/cpp/cursor",
    ):
        path = _ROOT / rel
        if path.exists():
            entries.append(str(path.resolve()))
    existing = os.environ.get("PYTHONPATH")
    if existing:
        entries.append(existing)
    return os.pathsep.join(entries)


def _write_fake_model_packages(tmp_path: Path) -> None:
    hub = tmp_path / "huggingface_hub"
    hub.mkdir()
    (hub / "__init__.py").write_text(
        "from .file_download import hf_hub_download\n",
        encoding="utf-8",
    )
    (hub / "file_download.py").write_text(
        textwrap.dedent(
            """
            import ctypes
            import os

            _LIBC = ctypes.CDLL(None)
            _LIBC.getpid.restype = ctypes.c_int


            def _fail_if_live_replay(operation):
                native_pid = int(_LIBC.getpid())
                retraced_pid = os.getpid()
                if native_pid != retraced_pid:
                    raise RuntimeError(
                        f"live {operation} during replay: "
                        f"native_pid={native_pid} retraced_pid={retraced_pid}"
                    )


            def hf_hub_download(repo_id, filename, **kwargs):
                _fail_if_live_replay("hf_hub_download")
                safe_repo = repo_id.replace("/", "--")
                return f"/fake-cache/{safe_repo}/{filename}"
            """
        ),
        encoding="utf-8",
    )

    llama = tmp_path / "llama_cpp"
    llama.mkdir()
    (llama / "__init__.py").write_text(
        "from .llama import Llama\n",
        encoding="utf-8",
    )
    (llama / "llama.py").write_text(
        textwrap.dedent(
            """
            import ctypes
            import os

            _LIBC = ctypes.CDLL(None)
            _LIBC.getpid.restype = ctypes.c_int


            def _fail_if_live_replay(operation):
                native_pid = int(_LIBC.getpid())
                retraced_pid = os.getpid()
                if native_pid != retraced_pid:
                    raise RuntimeError(
                        f"live {operation} during replay: "
                        f"native_pid={native_pid} retraced_pid={retraced_pid}"
                    )


            class Llama:
                def __init__(self, model_path, **kwargs):
                    _fail_if_live_replay("Llama.__init__")
                    self.model_path = model_path

                def create_chat_completion(self, messages, **kwargs):
                    _fail_if_live_replay("Llama.create_chat_completion")
                    return {
                        "choices": [
                            {"message": {"content": "cheapest-flight:LH1901"}}
                        ]
                    }
            """
        ),
        encoding="utf-8",
    )
