"""Test record/replay of module-level random helpers."""

import random

from tests.runner import Runner


def test_random_choice_replay_equals_record():
    runner = Runner()

    def work():
        return random.choice([True, False])

    recording = runner.record(work)
    replay_result = runner.replay(recording, work)

    assert recording.result == replay_result
