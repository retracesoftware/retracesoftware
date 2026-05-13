import importlib
import sys
import types
from pathlib import Path

import pytest

_AUTOENABLE = Path(__file__).resolve().parents[1] / "src" / "retracesoftware" / "autoenable.py"


@pytest.fixture
def autoenable(monkeypatch):
    monkeypatch.delenv("RETRACE_RECORDING", raising=False)
    monkeypatch.delenv("RETRACE_CONFIG", raising=False)
    name = "_retracesoftware_autoenable_under_test"
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, _AUTOENABLE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _fake_cpython(version, executable):
    return types.SimpleNamespace(
        __cpython_version__=version,
        executable=lambda: str(executable),
    )


def test_exec_retrace_python_uses_packaged_runtime(autoenable, monkeypatch, tmp_path):
    executable = tmp_path / "python"
    executable.write_text("")
    executable.chmod(0o755)

    monkeypatch.setattr(autoenable, "_has_retrace", lambda: False)
    monkeypatch.setitem(
        sys.modules,
        "retracesoftware_cpython",
        _fake_cpython(autoenable._python_version(), executable),
    )
    monkeypatch.setattr(sys, "path", ["", str(tmp_path / "site-packages")])

    captured = {}

    def execvpe(file, args, env):
        captured["file"] = file
        captured["args"] = args
        captured["env"] = env

    monkeypatch.setattr(autoenable.os, "execvpe", execvpe)

    autoenable._exec_retrace_python(["original-python", "-m", "retracesoftware", "--help"])

    assert captured["file"] == str(executable)
    assert captured["args"] == [str(executable), "-m", "retracesoftware", "--help"]
    assert captured["env"]["RETRACE_PYTHON_REEXECED"] == "1"
    assert captured["env"]["RETRACE_ORIGINAL_PYTHON"] == sys.executable
    assert captured["env"]["PYTHONHOME"] == autoenable._python_home()
    assert captured["env"]["PYTHONPATH"] == autoenable.os.pathsep.join(
        ["", str(tmp_path / "site-packages")]
    )


def test_patched_python_rejects_version_mismatch(autoenable, monkeypatch, tmp_path, capsys):
    executable = tmp_path / "python"
    executable.write_text("")

    monkeypatch.setitem(
        sys.modules,
        "retracesoftware_cpython",
        _fake_cpython("0.0.0", executable),
    )

    with pytest.raises(SystemExit):
        autoenable._patched_python()

    assert "does not match this Python" in capsys.readouterr().err


def test_reexec_guard_stops_second_unpatched_exec(autoenable, monkeypatch, capsys):
    monkeypatch.setenv("RETRACE_PYTHON_REEXECED", "1")
    monkeypatch.setattr(autoenable, "_has_retrace", lambda: False)

    with pytest.raises(SystemExit):
        autoenable._exec_retrace_python(["python", "-m", "retracesoftware"])

    assert "did not load retrace" in capsys.readouterr().err
