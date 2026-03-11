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

import pytest

from retracesoftware.install import install_for_pytest


@pytest.fixture(scope="session", autouse=True)
def _install_runtime():
    # Lazily install retrace for install-suite tests only.
    # This avoids polluting unrelated test groups during collection.
    runner = install_for_pytest()
    runtime = {
        "runner": runner,
        "system": runner._system,
    }
    yield runtime


# ── Pytest fixtures ──────────────────────────────────────────────
# Available to all tests in tests/ and its subdirectories.

@pytest.fixture
def system(_install_runtime):
    """The shared System instance with TOML-driven module patching."""
    return _install_runtime["system"]

@pytest.fixture
def runner(_install_runtime):
    """The shared TestRunner for record/replay tests."""
    return _install_runtime["runner"]
