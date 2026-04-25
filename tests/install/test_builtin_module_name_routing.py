import _io
import builtins
import importlib

from retracesoftware.install import install_retrace
from retracesoftware.install.importhook import install_import_hooks
from retracesoftware.proxy.system import System
from retracesoftware.modules import ModuleConfigResolver


def test_install_retrace_leaves_loaded_io_builtins_unpatched_by_default():
    """Default install avoids patching live `_io` builtins."""
    system = System()
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        assert repr(_io.open) == "<built-in function open>"
    finally:
        uninstall()


def test_install_retrace_can_patch_loaded_builtin_modules_via_user_override(
    monkeypatch, tmp_path
):
    """Explicit `_io` module overrides should still patch already-loaded builtins."""
    original_open = _io.open
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    (module_dir / "_io.toml").write_text(
        'proxy = ["open"]\nimmutable = ["BlockingIOError", "UnsupportedOperation"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("RETRACE_MODULES_PATH", str(module_dir))

    system = System()
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        assert _io.open is not original_open
        assert callable(_io.open)
        assert repr(_io.open).startswith("<wrapped_function ")
        assert repr(_io.open) != "<built-in function open>"
    finally:
        uninstall()


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


def test_install_import_hooks_disables_imports_without_unwrapping_args():
    calls = []

    def fake_disable_for(fn, *, unwrap_args=True):
        calls.append((fn, unwrap_args))
        return fn

    uninstall = install_import_hooks(fake_disable_for, lambda *args, **kwargs: None)
    try:
        assert (builtins.__import__, False) in calls
        assert (importlib.import_module, False) in calls
    finally:
        uninstall()
