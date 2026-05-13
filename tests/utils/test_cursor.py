import pytest

retrace = pytest.importorskip("retrace")
utils = pytest.importorskip("retracesoftware.utils")


OLD_UTILS_CURSOR_API = frozenset(
    {
        "CallCounter",
        "Cursor",
        "call_counter_disable_for",
        "current_call_counts",
        "current_cursor",
        "cursor_position",
        "cursor_snapshot",
        "install_call_counter",
        "install_cursor_hooks",
        "uninstall_call_counter",
        "uninstall_cursor_hooks",
        "yield_at_cursor",
    }
)


def test_utils_no_longer_exports_local_cursor_api():
    for name in OLD_UTILS_CURSOR_API:
        assert not hasattr(utils, name), name


def test_public_retrace_cursor_surface_is_available():
    for name in ("coordinates", "thread_delta", "call_at", "exclude", "include"):
        assert hasattr(retrace, name), name


def test_public_retrace_coordinates_smoke():
    coordinates = tuple(retrace.coordinates())

    assert len(coordinates) >= 2
    assert len(coordinates) % 2 == 0
    assert all(isinstance(item, int) for item in coordinates)
