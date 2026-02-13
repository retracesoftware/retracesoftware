"""Import hook machinery for the gate-based System.

Disables the proxy gates during module loading — the import machinery
is a hot path with heavy caching, and proxying it adds overhead for
negligible benefit — then re-enables them when a module's code is
actually executed (that's where user side-effects live and need to be
recorded/replayed).

After a module is executed, its namespace is patched according to its
TOML configuration (``proxy``, ``immutable``, ``bind``, ``disable``,
etc.).

Usage::

    from retracesoftware.install.importhook import install_import_hooks

    install_import_hooks(system, module_patcher)
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
    """

    # ── __import__ / importlib.import_module ──────────────────
    #
    # The import machinery is a hot path with heavy caching.
    # Disable the gates for the duration of each import so
    # patched types don't fire during module loading.
    builtins.__import__ = disable_for(builtins.__import__)
    importlib.import_module = disable_for(importlib.import_module)

    _orig_exec = builtins.exec

    # ── exec_module (LoaderBasics) ────────────────────────────
    #
    # The standard loader calls ``exec(code, module.__dict__)`` to
    # run a module's source.  We replace ``exec`` inside
    # ``_LoaderBasics.exec_module`` so that after the module's code
    # runs, its namespace is patched according to TOML config.

    def _exec_and_patch(source, globals=None, locals=None):
        _orig_exec(source, globals, locals)
        if globals is not None:
            module_patcher(globals, False)

    utils.update(_bootstrap_external._LoaderBasics, "exec_module",
                 utils.wrap_func_with_overrides,
                 exec=_exec_and_patch)

    # ── runpy._run_code ───────────────────────────────────────
    #
    # ``runpy.run_module`` and ``runpy.run_path`` use ``_run_code``
    # to execute the entry script (``python -m module``).  We
    # replace ``exec`` inside ``_run_code`` so the script's
    # namespace is also patched after execution.

    def _exec_and_patch_entry(source, globals=None, locals=None):
        _orig_exec(source, globals, locals)
        if globals is not None:
            module_patcher(globals, False)

    utils.update(runpy, "_run_code",
                 utils.wrap_func_with_overrides,
                 exec=_exec_and_patch_entry)

    # ── _imp.exec_dynamic / _imp.exec_builtin ─────────────────
    #
    # C extensions and built-in modules don't go through
    # exec_module — they're initialised by _imp directly.
    # Wrap them so the module is patched after init.

    _orig_exec_dynamic = _imp.exec_dynamic
    _orig_exec_builtin = _imp.exec_builtin

    def _wrap_exec(orig):
        def wrapper(module):
            orig(module)
            module_patcher(module.__dict__, False)
            return module
        return wrapper

    _imp.exec_dynamic = _wrap_exec(_orig_exec_dynamic)
    _imp.exec_builtin = _wrap_exec(_orig_exec_builtin)


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
