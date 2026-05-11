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


def test_dockertest_default_run_excludes_non_default_tags():
    """Known repro/perf/stress scenarios should not poison the default run."""

    run_py = (DOCKERTESTS / "run.py").read_text()
    datasette_tags = (DOCKERTESTS / "tests" / "datasette_server_test" / "tags").read_text()
    flask_basic_tags = (DOCKERTESTS / "tests" / "flask_basic_test" / "tags").read_text()
    flask_server_tags = (DOCKERTESTS / "tests" / "flask_server_test" / "tags").read_text()

    assert '"manual": "use --include-manual or --tags manual to run them"' in run_py
    assert '"perf": "use --include-perf or --tags perf to run them"' in run_py
    assert '"stress": "use --include-stress or --tags stress to run them"' in run_py
    assert "--include-manual" in run_py
    assert "--include-stress" in run_py
    assert "manual" in datasette_tags
    assert "regression" in datasette_tags
    assert "manual" in flask_basic_tags
    assert "regression" in flask_basic_tags
    assert "manual" in flask_server_tags
    assert "regression" in flask_server_tags


def test_dockertest_smoke_tier_keeps_representative_record_replay_coverage():
    """Push CI should stay fast without dropping real record/replay coverage."""

    run_py = (DOCKERTESTS / "run.py").read_text()
    workflow = (REPO_ROOT / ".github" / "workflows" / "docker-test.yml").read_text()

    assert "--smoke" in run_py
    assert "python run.py --clean --smoke --image retracesoftware-test" in workflow
    for name in (
        "simple_test",
        "datetime_test",
        "asyncio_test",
        "requests_test",
        "flask_test",
        "fastapi_test",
        "psycopg2_test",
        "subprocess_terminate_wait_timeout_test",
        "llama_cpp_model_boundary_test",
    ):
        assert f'"{name}"' in run_py
