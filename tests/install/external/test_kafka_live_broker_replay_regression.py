"""Regression for kafka-python replay misrouting time and lock callbacks."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest
from retracesoftware.tape import checksums


_KAFKA_TIME_LOCK_PIDFILE_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "kafka_time_lock_pidfile.fixture"
)

_FIXTURE_WORKDIR = Path(
    "/tmp/retrace-kafka-fixture-public-path-placeholder-"
    "00000000000000000000000000000000000000000000000000"
)


_KAFKA_SCRIPT = """
import os
import time
import uuid

from kafka import KafkaAdminClient, KafkaConsumer, KafkaProducer
from kafka.admin import NewTopic
from kafka.errors import TopicAlreadyExistsError


BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:19092")


def wait_for_kafka():
    last_error = None
    for _ in range(90):
        admin = None
        try:
            admin = KafkaAdminClient(
                bootstrap_servers=BOOTSTRAP,
                client_id="retrace-wait",
                request_timeout_ms=3000,
            )
            admin.list_topics()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(0.5)
        finally:
            if admin is not None:
                admin.close()
    raise RuntimeError(f"kafka did not become ready: {last_error}") from last_error


def main():
    print("=== kafka_live_broker_test ===")
    wait_for_kafka()

    topic = f"retrace-live-{uuid.uuid4().hex}"
    admin = KafkaAdminClient(bootstrap_servers=BOOTSTRAP, client_id="retrace-admin")
    try:
        try:
            admin.create_topics([NewTopic(topic, num_partitions=1, replication_factor=1)])
        except TopicAlreadyExistsError:
            pass
    finally:
        admin.close()

    producer = KafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        client_id="retrace-producer",
        key_serializer=lambda value: value.encode("utf-8"),
        value_serializer=lambda value: value.encode("utf-8"),
    )
    metadata = producer.send(topic, key="invoice", value="created").get(timeout=15)
    producer.flush()
    producer.close()
    assert metadata.topic == topic

    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=BOOTSTRAP,
        group_id=f"retrace-group-{uuid.uuid4().hex}",
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        consumer_timeout_ms=10000,
        key_deserializer=lambda value: value.decode("utf-8"),
        value_deserializer=lambda value: value.decode("utf-8"),
    )
    try:
        messages = list(consumer)
    finally:
        consumer.close()

    assert [(message.key, message.value) for message in messages] == [
        ("invoice", "created")
    ]
    print("kafka live broker record/replay scenario ok")


if __name__ == "__main__":
    main()
"""


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )


def _write_rebased_kafka_pidfile_fixture(
    *,
    source: Path,
    target: Path,
    cwd: Path,
) -> None:
    with source.open("rb") as src:
        shebang = src.readline()
        header = json.loads(src.readline())
        body = src.read()

    replay_env = os.environ.copy()
    replay_env.pop("RETRACE_RECORDING", None)
    replay_env.pop("RETRACE_CONFIG", None)
    replay_env.pop("RETRACE_SKIP_CHECKSUMS", None)
    replay_env["PYTHONFAULTHANDLER"] = "1"
    replay_env["RETRACE_CONFIG"] = "debug"
    replay_env["KAFKA_BOOTSTRAP_SERVERS"] = "127.0.0.1:19092"
    replay_env["HOME"] = "/Users/retraceuser00000"

    header["cwd"] = str(cwd)
    header["executable"] = sys.executable
    header["python_version"] = sys.version
    header["checksums"] = checksums()
    header["env"] = replay_env
    header["sys_path"] = [str(cwd)] + [path for path in sys.path if path]

    with target.open("wb") as dst:
        dst.write(shebang)
        dst.write(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        dst.write(b"\n")
        dst.write(body)
    target.chmod(0o755)


@pytest.mark.xfail(
    strict=True,
    reason="kafka-python replay currently misroutes time and lock callback messages",
)
def test_kafka_captured_pidfile_replay_keeps_time_and_lock_order() -> None:
    """Regression for the current live Kafka replay failure.

    The captured replay currently fails with:

        Checkpoint difference:
        {'function': wrapped_function:RLock.acquire ...}
        was expecting {'function': wrapped_function:time.time ...}
    """

    pytest.importorskip("kafka")

    workdir = _FIXTURE_WORKDIR
    shutil.rmtree(workdir, ignore_errors=True)
    workdir.mkdir(parents=True)
    (workdir / "test.py").write_text(textwrap.dedent(_KAFKA_SCRIPT), encoding="utf-8")

    pidfile = workdir / "kafka_time_lock_repro.bin"
    _write_rebased_kafka_pidfile_fixture(
        source=_KAFKA_TIME_LOCK_PIDFILE_FIXTURE,
        target=pidfile,
        cwd=workdir,
    )

    replay_env = os.environ.copy()
    replay_env.pop("RETRACE_RECORDING", None)
    replay_env.pop("RETRACE_SKIP_CHECKSUMS", None)
    replay_env["PYTHONFAULTHANDLER"] = "1"
    replay_env["RETRACE_CONFIG"] = "debug"

    replay = _run(
        [sys.executable, "-m", "retracesoftware", "--recording", str(pidfile)],
        cwd=workdir,
        env=replay_env,
    )
    combined = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        f"kafka PidFile replay diverged (exit {replay.returncode})\n"
        f"stdout:\n{replay.stdout}\n"
        f"stderr:\n{replay.stderr}"
    )
    assert "wrapped_function:time.time" not in combined
    assert "<_thread.lock object" not in combined
    assert "Checkpoint difference:" not in combined
