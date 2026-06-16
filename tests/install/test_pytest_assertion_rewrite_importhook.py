from __future__ import annotations

import sys
import types

from retracesoftware.install.importhook import install_import_hooks


def test_importhook_patches_modules_loaded_by_pytest_assertion_rewrite(
    monkeypatch,
):
    rewrite_module = types.ModuleType("_pytest.assertion.rewrite")

    class AssertionRewritingHook:
        def exec_module(self, module):
            module.loaded_by_pytest_rewrite = True

    rewrite_module.AssertionRewritingHook = AssertionRewritingHook
    monkeypatch.setitem(sys.modules, "_pytest.assertion.rewrite", rewrite_module)

    patched_modules = []

    def disable_for(function, *, unwrap_args=True):
        return function

    def module_patcher(namespace, update_refs, module_name=None):
        patched_modules.append(module_name)
        namespace["patched_by_retrace"] = True

    original_exec_module = AssertionRewritingHook.exec_module
    uninstall = install_import_hooks(disable_for, module_patcher)
    try:
        loaded = types.ModuleType("pytest_loaded_plugin")
        loaded.__spec__ = types.SimpleNamespace(name="pytest_loaded_plugin")

        AssertionRewritingHook().exec_module(loaded)

        assert loaded.loaded_by_pytest_rewrite is True
        assert loaded.patched_by_retrace is True
        assert patched_modules == ["pytest_loaded_plugin"]
    finally:
        uninstall()

    assert AssertionRewritingHook.exec_module is original_exec_module
