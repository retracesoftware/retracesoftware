"""Test record/replay of time operations.

Verifies that time.time(), time.monotonic(), etc. return the same
values on replay as they did during recording.
"""
import time


def test_time_time(system, runner):
    """time.time() records and replays the same value."""
    patched_time = system.patch(time.time)

    def do_time():
        return patched_time()

    runner.run(do_time)


def test_monotonic(system, runner):
    """time.monotonic() records and replays the same value."""
    patched_mono = system.patch(time.monotonic)

    def do_monotonic():
        return patched_mono()

    runner.run(do_monotonic)
