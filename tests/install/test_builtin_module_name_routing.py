import io
import _io

from retracesoftware.modules import ModuleConfigResolver


def test_install_for_pytest_patches_loaded_builtin_modules():
    """Already-loaded builtins should be patched by their sys.modules key."""
    assert "BoundGate" in repr(_io.open)
    assert io.open is _io.open


def test_io_single_module_config_preserves_root_immutable_directive():
    cfg = ModuleConfigResolver()["_io"]

    assert cfg["immutable"] == ["BlockingIOError", "UnsupportedOperation"]


def test_single_module_parser_accepts_replay_materialize(monkeypatch, tmp_path):
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "demo.toml").write_text(
        'proxy = ["open"]\nreplay_materialize = ["open"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RETRACE_MODULES_PATH", str(module_dir))

    cfg = ModuleConfigResolver()

    assert cfg["demo"]["replay_materialize"] == ["open"]
