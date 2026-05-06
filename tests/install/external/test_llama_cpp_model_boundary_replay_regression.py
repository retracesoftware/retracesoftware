"""Regression coverage for local LLM model boundaries.

The invoice-parser/flight-search demos showed that PidFile replay was still
executing HuggingFace cache lookup and llama_cpp model initialization live.
This test uses tiny fake ``huggingface_hub`` and ``llama_cpp`` packages with the
same public import shape. The fake model-boundary functions compare patched
``os.getpid()`` with a direct libc ``getpid()`` call; those match during record,
but differ during replay if Retrace live-enters the boundary body instead of
returning the recorded result.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import textwrap

import pytest

from tests.helpers import run_record, run_replay


_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "missing built-in module interception for huggingface_hub and "
        "llama_cpp lets replay live-run model download/load/generation"
    ),
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "missing built-in module interception for huggingface_hub and "
        "llama_cpp lets replay live-run model download/load/generation"
    ),
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
