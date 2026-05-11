"""Regression coverage for multiprocessing spawn PidFile replay shutdown order."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers import record_and_replay_pth_pidfile, tail


@pytest.mark.xfail(
    strict=True,
    reason=(
        "PidFile replay currently desyncs after multiprocessing spawn child "
        "results have replayed"
    ),
)
def test_multiprocessing_spawn_pth_pidfile_replay_keeps_checkpoint_alignment(
    tmp_path: Path,
) -> None:
    record, replay = record_and_replay_pth_pidfile(
        tmp_path=tmp_path,
        script_name="multiprocessing_spawn_repro.py",
        script_source="""
            import multiprocessing as mp
            import os


            def worker(input_queue, output_queue):
                values = input_queue.get(timeout=5)
                output_queue.put(
                    {
                        "pid_is_child": os.getpid() != os.getppid(),
                        "total": sum(values),
                        "count": len(values),
                    }
                )


            def main():
                print("=== multiprocessing_spawn_repro ===", flush=True)
                context = mp.get_context("spawn")
                input_queue = context.Queue()
                output_queue = context.Queue()
                input_queue.put([2, 3, 5, 7])

                process = context.Process(
                    target=worker,
                    args=(input_queue, output_queue),
                )
                process.start()
                result = output_queue.get(timeout=10)
                process.join(timeout=10)

                assert process.exitcode == 0
                assert result["total"] == 17
                assert result["count"] == 4
                assert result["pid_is_child"] is True
                print(f"result={result}", flush=True)
                print("multiprocessing spawn ok", flush=True)


            if __name__ == "__main__":
                main()
        """,
        timeout=45,
    )
    combined_replay = replay.stdout + replay.stderr

    assert replay.returncode == 0, (
        "multiprocessing spawn pidfile replay diverged "
        f"(exit {replay.returncode})\n"
        f"record stdout:\n{tail(record.stdout)}\n"
        f"record stderr tail:\n{tail(record.stderr)}\n"
        f"replay stdout:\n{tail(replay.stdout)}\n"
        f"replay stderr tail:\n{tail(replay.stderr)}"
    )
    assert replay.stdout == record.stdout
    assert "Checkpoint difference: 'SYNC'" not in combined_replay
    assert (
        "was expecting type:retracesoftware.protocol.messages.CheckpointMessage"
        not in combined_replay
    )
