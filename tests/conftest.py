"""Shared fixtures for retracesoftware tests."""
import os
from pathlib import Path
import sys

os.environ["RETRACE_DEBUG"] = "1"

import shutil
import tempfile

import pytest

from tests.helpers import run_record, run_replay  # noqa: F401 — re-exported for fixtures


def _append_mesonpy_editable_skip(path: Path) -> None:
    if not path.exists():
        return

    current = os.environ.get("MESONPY_EDITABLE_SKIP", "")
    parts = [entry for entry in current.split(os.pathsep) if entry]
    value = str(path)
    if value not in parts:
        parts.append(value)
        os.environ["MESONPY_EDITABLE_SKIP"] = os.pathsep.join(parts)


def _prepend_python_path(path: Path) -> None:
    if not path.exists():
        return

    value = str(path)
    if value not in sys.path:
        sys.path.insert(0, value)

    current = os.environ.get("PYTHONPATH", "")
    parts = [entry for entry in current.split(os.pathsep) if entry]
    if value not in parts:
        os.environ["PYTHONPATH"] = os.pathsep.join([value, *parts]) if parts else value


_REPO_ROOT = Path(__file__).resolve().parents[1]
_BUILD_TAG = f"cp{sys.version_info.major}{sys.version_info.minor}{getattr(sys, 'abiflags', '')}"
_LOCAL_BUILD_DIR = _REPO_ROOT / "build" / _BUILD_TAG


def _configure_local_imports() -> None:
    # Import local Python sources and native extensions directly during tests.
    # This avoids Meson editable-loader rebuilds in both pytest and child
    # Python processes spawned by integration tests.
    _prepend_python_path(_REPO_ROOT / "src")

    if _LOCAL_BUILD_DIR.exists():
        _append_mesonpy_editable_skip(_LOCAL_BUILD_DIR)
        for relpath in (
            Path("cpp") / "functional",
            Path("cpp") / "utils",
            Path("cpp") / "stream",
            Path("cpp") / "cursor",
        ):
            _prepend_python_path(_LOCAL_BUILD_DIR / relpath)


_configure_local_imports()
_append_mesonpy_editable_skip(
    Path(__file__).resolve().parents[2] / "utils" / "build" / _BUILD_TAG
)


_TEST_GROUP_ORDER = {
    "functional": 0,
    "utils": 1,
    "proxy": 2,
    "stream": 3,
    "replay": 4,
    "scripts": 5,
    "install": 6,
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
