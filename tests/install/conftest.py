"""Shared fixtures for install tests.

Uses the proxy System directly with TOML-driven module patching.

Walks ``sys.modules`` and patches every loaded module that has a
TOML config, except those in ``_SKIP``.

Import machinery is wrapped with ``disable_for`` so that lock
operations from lazy imports (``_ModuleLock.acquire`` etc.) are
never recorded — they would cause tape misalignment on replay
because modules cached after record don't re-acquire import locks.

``_SKIP`` contains modules whose proxy directives cause problems
in a test environment where libraries are already loaded:

- **posix / _posixsubprocess** — file-I/O functions that libraries
  cache (cert loading, path checks).  Recorded call sequences
  diverge on replay because caches are warm.
- **_frozen_importlib_external** — import machinery internals.

In production the import hooks patch modules *before* any user
code runs, so caching divergence doesn't apply.
"""
import os
os.environ["RETRACE_DEBUG"] = "1"

import sys
import builtins
import pytest

from retracesoftware.proxy.system import System
from retracesoftware.install import TestRunner
from retracesoftware.install.patcher import install_hash_patching, patch
from retracesoftware.install.hooks import install_trace_hooks, init_weakref
from retracesoftware.modules import ModuleConfigResolver

_system = System()
install_hash_patching(_system)
install_trace_hooks(_system.disable_for)
init_weakref()

# Disable retrace gates during imports so import-machinery lock
# operations (e.g. _ModuleLock.acquire from lazy imports) are never
# recorded.  In production install_import_hooks does this AND
# re-enables retrace for module exec; for testing we keep it
# disabled throughout — module-level side effects don't matter here.
#
# We use a pure-Python save/restore rather than system.disable_for
# because the C-level ApplyWith doesn't handle the deeply recursive
# __import__ call chain (nested imports crash with NULL return).
_orig_import = builtins.__import__
_ext_gate = _system._external
_int_gate = _system._internal

def _disabled_import(*args, **kwargs):
    ext_saved = _ext_gate.executor
    int_saved = _int_gate.executor
    _ext_gate.executor = None
    _int_gate.executor = None
    try:
        return _orig_import(*args, **kwargs)
    finally:
        _ext_gate.executor = ext_saved
        _int_gate.executor = int_saved

builtins.__import__ = _disabled_import

# ── TOML-driven module patching ──────────────────────────────────
# Immutable types (int, str, list, etc.) are declared in stdlib.toml
# under [builtins] and applied by patch() below — no manual list needed.
module_config = ModuleConfigResolver()

# Modules to skip in test environment:
# - posix/_posixsubprocess — file-I/O caching in libraries
# - _frozen_importlib_external — import machinery, dangerous to proxy
_SKIP = {
    'posix', '_posixsubprocess',
    '_frozen_importlib_external',
}

def module_patcher(namespace, update_refs):
    name = namespace.get('__name__')
    if name and name in module_config:
        try:
            patch(namespace, module_config[name], _system, update_refs=update_refs)
        except Exception:
            pass  # skip modules whose directives aren't supported yet (e.g. bind)

for modname in module_config.keys():
    if modname in sys.modules and modname not in _SKIP:
        module_patcher(sys.modules[modname].__dict__, True)

_runner = TestRunner(_system)


# ── Pytest fixtures ──────────────────────────────────────────────
# Available to all tests in tests/ and its subdirectories.

@pytest.fixture
def system():
    """The shared System instance with TOML-driven module patching."""
    return _system

@pytest.fixture
def runner():
    """The shared TestRunner for record/replay tests."""
    return _runner
