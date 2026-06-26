"""Runtime smoke for Seldon-derived import and fork shapes."""

from __future__ import annotations

import os
import signal
import sys
import time


def _exercise_import_stack() -> None:
    import _pyio
    import dill
    import multiprocess

    print(
        f"imports ok: {_pyio.__name__} dill={dill.__version__} multiprocess={multiprocess.__version__}",
        flush=True,
    )


def _fork_and_stop_child(mode: str) -> None:
    pid = os.fork()
    if pid == 0:
        print(f"child mode={mode}", flush=True)
        if mode == "pause":
            signal.pause()
        elif mode == "busy":
            while True:
                pass
        else:
            raise AssertionError(f"unknown mode {mode!r}")
        sys.exit(0)

    time.sleep(0.1)
    os.kill(pid, signal.SIGTERM)
    os.waitpid(pid, 0)
    print(f"parent stopped child mode={mode}", flush=True)


def main() -> None:
    print("=== seldon_followup_runtime_shapes_test ===", flush=True)
    _exercise_import_stack()
    _fork_and_stop_child("pause")
    _fork_and_stop_child("busy")
    print("seldon follow-up runtime shapes ok", flush=True)


if __name__ == "__main__":
    main()
