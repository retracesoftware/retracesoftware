"""Test record/replay of time operations.

Verifies that time.time(), time.monotonic(), etc. return the same
values on replay as they did during recording.
"""
import time

import pytest


@pytest.fixture
def time_runtime(runner, system):
    return runner, system


def test_time_time(system, runner):
    """time.time() records and replays the same value."""
    patched_time = system.patch(time.time)

    def do_time():
        return patched_time()

    runner.run(do_time)


def test_time_time_replay_equals_record_explicitly(time_runtime):
    """Explicitly assert replayed time.time() equals the recorded value."""
    runner, system = time_runtime
    patched_time = system.patch(time.time)

    def do_time():
        return patched_time()

    recording = runner.record(do_time)
    replay_result = runner.replay(recording, do_time)

    assert recording.result == replay_result


def test_monotonic(system, runner):
    """time.monotonic() records and replays the same value."""
    patched_mono = system.patch(time.monotonic)

    def do_monotonic():
        return patched_mono()

    runner.run(do_monotonic)
