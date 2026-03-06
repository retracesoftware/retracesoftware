"""Shared fixtures for retracesoftware tests."""
import os
os.environ["RETRACE_DEBUG"] = "1"

import shutil
import tempfile

import pytest

from tests.helpers import run_record, run_replay  # noqa: F401 — re-exported for fixtures


@pytest.fixture
def tmpdir():
    """A fresh temporary directory, cleaned up after the test."""
    d = tempfile.mkdtemp(prefix="retrace_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)
