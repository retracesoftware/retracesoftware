"""Shared fixtures for retracesoftware tests."""
import os
os.environ["RETRACE_DEBUG"] = "1"

import shutil
import tempfile
from pathlib import Path

import pytest

from tests.helpers import run_record, run_replay  # noqa: F401 — re-exported for fixtures


_TEST_GROUP_ORDER = {
    "functional": 0,
    "utils": 1,
    "proxy": 2,
    "stream": 3,
    "install": 4,
    "replay": 5,
    "scripts": 6,
    "__root__": 7,
}


def _group_name(item: pytest.Item) -> str:
    path = Path(str(item.fspath))
    try:
        rel = path.relative_to(Path(__file__).parent)
    except ValueError:
        return "__root__"
    return rel.parts[0] if len(rel.parts) > 1 else "__root__"


def pytest_collection_modifyitems(session, config, items):
    """Run tests in deterministic, directory-based groups.

    Functional tests run first. Remaining groups follow the explicit
    order above, with unknown groups ordered after the known ones.
    """

    def sort_key(item: pytest.Item):
        group = _group_name(item)
        return (
            _TEST_GROUP_ORDER.get(group, 100),
            group,
            str(item.fspath),
            getattr(item, "originalname", item.name),
        )

    items.sort(key=sort_key)


@pytest.fixture
def tmpdir():
    """A fresh temporary directory, cleaned up after the test."""
    d = tempfile.mkdtemp(prefix="retrace_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)
