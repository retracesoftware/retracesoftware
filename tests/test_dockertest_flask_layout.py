from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
FLASK_TEST = REPO_ROOT / "dockertests" / "tests" / "flask_test"
DOCKERTESTS = REPO_ROOT / "dockertests"


def test_flask_test_uses_finite_client_side_script_pipeline():
    """A stray client.py makes runtest.sh pick the long-running server pipeline."""

    compose = (FLASK_TEST / "docker-compose.yml").read_text()
    client = (FLASK_TEST / "test.py").read_text()
    requirements = (FLASK_TEST / "requirements.txt").read_text().splitlines()

    assert not (FLASK_TEST / "client.py").exists()
    assert (FLASK_TEST / "app.py").exists()
    assert "requests" in requirements
    assert "requests.Session()" in client
    assert "server-record:" in compose
    assert "python /app/test/app.py" in compose
    assert "FLASK_URL: http://server-record:5000" in compose


def test_dockertest_replay_services_do_not_seed_recording_environment():
    """Replay should start from replay-only env, then restore recorded app env."""

    for compose_file in (
        DOCKERTESTS / "docker-compose.base.yml",
        DOCKERTESTS / "docker-compose.server-base.yml",
    ):
        compose = compose_file.read_text()
        replay_block = compose.split("\n  replay:", 1)[1].split("\n  cleanup:", 1)[0]

        assert "RETRACE_FORMAT" not in replay_block
        assert "RETRACE_REPLAY_BIN" not in replay_block
