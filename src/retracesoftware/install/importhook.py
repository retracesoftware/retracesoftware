"""Import hook machinery for the gate-based System.

Disables the proxy gates during module loading — the import machinery
is a hot path with heavy caching, and proxying it adds overhead for
negligible benefit — then re-enables them when a module's code is
actually executed (that's where user side-effects live and need to be
recorded/replayed).

After a module is executed, its namespace is patched according to its
TOML configuration (``proxy``, ``immutable``, ``bind``, ``disable``,
etc.).

Returns an uninstall callable so the hooks can be cleanly removed
(e.g. for running record/replay inside pytest).

Usage::

    from retracesoftware.install.importhook import install_import_hooks

    uninstall = install_import_hooks(system.disable_for, module_patcher)
    # ... run ...
    uninstall()
"""

import sys
import builtins
import _imp
import runpy
import importlib
import importlib._bootstrap_external as _bootstrap_external

import retracesoftware.utils as utils


def install_import_hooks(disable_for, module_patcher):
    """Hook the import machinery to disable gates during import.

    Parameters
    ----------
    disable_for : callable(fn) → fn
        Wraps a function so the proxy gates are temporarily cleared
        for its duration.  Typically ``system.disable_for``.
    module_patcher : callable(namespace_dict, update_refs: bool) → None
        Called after a module is loaded to apply TOML-derived patches
        to its ``__dict__``.  Typically built from
        ``patcher.patch`` + ``ModuleConfigResolver``.

    Returns
    -------
    callable
        An uninstall function that restores all patched entry points.
    """

    # ── save originals ────────────────────────────────────────
    orig_import = builtins.__import__
    orig_import_module = importlib.import_module
    orig_exec_dynamic = _imp.exec_dynamic
    orig_exec_builtin = _imp.exec_builtin

    # We need to capture the original exec_module before wrapping.
    # utils.update returns the old value — but we capture the whole
    # _LoaderBasics.exec_module method for clean restore.
    orig_exec_module = _bootstrap_external._LoaderBasics.exec_module

    # We also need the original _run_code.
    orig_run_code = runpy._run_code

    # ── __import__ / importlib.import_module ──────────────────
    builtins.__import__ = disable_for(builtins.__import__)
    importlib.import_module = disable_for(importlib.import_module)

    _orig_exec = builtins.exec

    # ── exec_module (LoaderBasics) ────────────────────────────
    def _exec_and_patch(source, globals=None, locals=None):
        _orig_exec(source, globals, locals)
        if globals is not None:
            module_patcher(globals, False)

    utils.update(_bootstrap_external._LoaderBasics, "exec_module",
                 utils.wrap_func_with_overrides,
                 exec=_exec_and_patch)

    # ── runpy._run_code ───────────────────────────────────────
    def _exec_and_patch_entry(source, globals=None, locals=None):
        _orig_exec(source, globals, locals)
        if globals is not None:
            module_patcher(globals, False)

    utils.update(runpy, "_run_code",
                 utils.wrap_func_with_overrides,
                 exec=_exec_and_patch_entry)

    # ── _imp.exec_dynamic / _imp.exec_builtin ─────────────────
    def _wrap_exec(orig):
        def wrapper(module):
            orig(module)
            module_patcher(module.__dict__, False)
            return module
        return wrapper

    _imp.exec_dynamic = _wrap_exec(orig_exec_dynamic)
    _imp.exec_builtin = _wrap_exec(orig_exec_builtin)

    # ── uninstall ─────────────────────────────────────────────
    def uninstall():
        builtins.__import__ = orig_import
        importlib.import_module = orig_import_module
        _imp.exec_dynamic = orig_exec_dynamic
        _imp.exec_builtin = orig_exec_builtin
        _bootstrap_external._LoaderBasics.exec_module = orig_exec_module
        runpy._run_code = orig_run_code

    return uninstall


def patch_already_loaded(module_patcher, module_config):
    """Patch modules that were imported before the hooks were installed.

    Parameters
    ----------
    module_patcher : callable(namespace_dict, update_refs: bool) → None
        Same patcher as passed to ``install_import_hooks``.
    module_config : ModuleConfigResolver
        The TOML config resolver — ``module_config.keys()`` yields the
        module names that have configurations.
    """
    for modname in module_config.keys():
        if modname in sys.modules:
            module_patcher(sys.modules[modname].__dict__, True)
