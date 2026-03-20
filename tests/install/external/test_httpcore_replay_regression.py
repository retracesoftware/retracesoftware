"""Regression: httpcore HTTPS replay fails after successful record.

Reproduces the dockertest failure in a focused install/external test:
- record succeeds for an HTTPS httpcore request
- replay fails inside TLS startup instead of reproducing record behavior

This keeps the test at the same abstraction level as other install/external
record+replay tests while exercising the TLS/socket path that currently
diverges.
"""

from __future__ import annotations

import pytest

httpcore = pytest.importorskip("httpcore")


URL = "https://httpbin.org/get?patient_id=p123&status=active"


def _httpcore_fetch(url: str) -> tuple[int, int]:
    with httpcore.ConnectionPool() as client:
        response = client.request("GET", url, headers={"Accept": "application/json"})
        return response.status, len(response.content)


def test_httpcore_https_record_replay_does_not_diverge(runner):
    recording = runner.record(_httpcore_fetch, URL)
    assert recording.error is None
    assert recording.result[0] == 200

    replay_result = runner.replay(recording, _httpcore_fetch, URL)
    assert replay_result[0] == 200
