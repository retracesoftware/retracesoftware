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


def test_dockertest_base_requirements_do_not_pull_external_replay_binary():
    """Dockertests should exercise the local checkout's packaged replay binary."""

    requirements = (DOCKERTESTS / "base-requirements.txt").read_text()

    assert "retracesoftware_replay" not in requirements


def test_dockertest_default_image_has_retrace_build_toolchain():
    """The default dockertest image must include Go for local source installs."""

    dockerfile = (DOCKERTESTS / "Dockerfile.test").read_text()
    run_py = (DOCKERTESTS / "run.py").read_text()
    runtest = (DOCKERTESTS / "runtest.sh").read_text()
    base_compose = (DOCKERTESTS / "docker-compose.base.yml").read_text()
    server_compose = (DOCKERTESTS / "docker-compose.server-base.yml").read_text()

    assert 'DEFAULT_TEST_IMAGE = os.environ.get("RETRACE_DEFAULT_TEST_IMAGE", "retracesoftware-test")' in run_py
    assert 'DEFAULT_TEST_IMAGE="${RETRACE_DEFAULT_TEST_IMAGE:-retracesoftware-test}"' in runtest
    assert "docker build -t \"$TEST_IMAGE\" -f Dockerfile.test .." in runtest
    assert "FROM golang:1.25-bookworm AS go" in dockerfile
    assert 'COPY --from=go /usr/local/go /usr/local/go' in dockerfile
    assert "${TEST_IMAGE:-retracesoftware-test}" in base_compose
    assert "${TEST_IMAGE:-retracesoftware-test}" in server_compose
