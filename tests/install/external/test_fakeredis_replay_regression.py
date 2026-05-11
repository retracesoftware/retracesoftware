"""Regression coverage for fakeredis/redis replay message ordering."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import record_and_replay_pth_pidfile, tail


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PidFile replay currently misroutes fakeredis time/lock cleanup "
        "messages"
    ),
)
def test_fakeredis_pth_pidfile_replay_keeps_time_and_lock_messages_aligned(
    tmp_path: Path,
) -> None:
    pytest.importorskip("fakeredis")

    record, replay = record_and_replay_pth_pidfile(
        tmp_path=tmp_path,
        script_name="fakeredis_repro.py",
        script_source="""
            import fakeredis


            def main():
                print("=== fakeredis_repro ===", flush=True)
                client = fakeredis.FakeRedis(decode_responses=True)
                client.hset("user:1", mapping={"name": "Ada", "role": "engineer"})
                client.lpush("jobs", "build", "test")
                client.incrby("counter", 3)

                user = client.hgetall("user:1")
                jobs = [client.rpop("jobs"), client.rpop("jobs")]
                counter = client.get("counter")

                assert user == {"name": "Ada", "role": "engineer"}
                assert jobs == ["build", "test"]
                assert counter == "3"
                print(f"user={user}", flush=True)
                print(f"jobs={jobs}", flush=True)
                print("redis fakeredis ok", flush=True)


            if __name__ == "__main__":
                main()
        """,
        timeout=45,
    )
    combined_replay = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        f"fakeredis pidfile replay diverged (exit {replay.returncode})\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr tail:\n{tail(record.stderr)}\n"
        f"replay stdout:\n{tail(replay.stdout)}\n"
        f"replay stderr tail:\n{tail(replay.stderr)}"
    )
    assert replay.stdout == record.stdout
    assert "wrapped_function:time.time" not in combined_replay
    assert "Checkpoint difference:" not in combined_replay
