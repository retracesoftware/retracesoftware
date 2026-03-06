"""Test record/replay of posix / os operations.

Verifies that filesystem and process info calls through the posix
C extension are recorded and replayed correctly.
"""
import os


def test_getcwd(system, runner):
    """os.getcwd() records and replays the same value."""
    patched_getcwd = system.patch(os.getcwd)

    def do_getcwd():
        return patched_getcwd()

    runner.run(do_getcwd)


def test_getpid(system, runner):
    """os.getpid() records and replays the same value."""
    patched_getpid = system.patch(os.getpid)

    def do_getpid():
        return patched_getpid()

    runner.run(do_getpid)


def test_urandom(system, runner):
    """os.urandom() records and replays the same bytes."""
    patched_urandom = system.patch(os.urandom)

    def do_urandom():
        return patched_urandom(16)

    runner.run(do_urandom)
