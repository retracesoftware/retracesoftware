"""Test record/replay of posix / os operations.

Verifies that filesystem and process info calls through the posix
C extension are recorded and replayed correctly.
"""
import os

from tests.runner import Runner, retrace_test


@retrace_test
def test_getcwd():
    """os.getcwd() records and replays the same value."""
    value = os.getcwd()
    assert value
    return value


@retrace_test
def test_getpid():
    """os.getpid() records and replays the same value."""
    value = os.getpid()
    assert isinstance(value, int)
    assert value > 0
    return value


def test_urandom():
    """os.urandom() records and replays the same bytes."""
    state = {}

    def configure_system(system):
        state["urandom"] = system.patch(os.urandom)

    runner = Runner(configure_system=configure_system)

    def do_urandom():
        value = state["urandom"](16)
        assert isinstance(value, bytes)
        assert len(value) == 16
        return value

    runner.run(do_urandom)


def test_open_materializes_live_file_descriptor(tmp_path):
    """os.open() replay returns a live fd for C-level file wrappers."""
    path = tmp_path / "materialized-fd.txt"

    def work():
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR | os.O_TRUNC, 0o600)
        try:
            os.write(fd, b"ok")
            os.lseek(fd, 0, os.SEEK_SET)
            return os.read(fd, 2)
        finally:
            os.close(fd)

    assert Runner(matrix="core").run(work) == b"ok"
