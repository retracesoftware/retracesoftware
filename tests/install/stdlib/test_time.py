"""Test record/replay of time operations.

Verifies that time.time(), time.monotonic(), etc. return the same
values on replay as they did during recording.
"""
import time

from tests.runner import Runner, retrace_test


@retrace_test
def test_time_time():
    """time.time() records and replays the same value."""
    value = time.time()
    assert isinstance(value, float)
    return value


def test_time_time_replay_equals_record_explicitly():
    """Explicitly assert replayed time.time() equals the recorded value."""
    runner = Runner()

    def work():
        return time.time()

    recording = runner.record(work)
    replay_result = runner.replay(recording, work)

    assert recording.result == replay_result


def test_strftime_without_tuple_replays_recorded_wall_clock():
    """time.strftime(format) records implicit current-time formatting."""
    runner = Runner()

    def work():
        return time.strftime("%Y-%m-%d %H:%M:%S")

    recording = runner.record(work)

    deadline = time.time() + 2
    while time.strftime("%Y-%m-%d %H:%M:%S") == recording.result:
        if time.time() > deadline:
            raise AssertionError("wall-clock second did not advance")
        time.sleep(0.05)

    replay_result = runner.replay(recording, work)

    assert recording.result == replay_result


@retrace_test
def test_monotonic():
    """time.monotonic() records and replays the same value."""
    value = time.monotonic()
    assert isinstance(value, float)
    return value
