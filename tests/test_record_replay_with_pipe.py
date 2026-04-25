"""End-to-end record/replay through a named pipe for each script in scripts/."""
import os
import tempfile
import shutil
from pathlib import Path

import pytest

from run_record_replay import record_then_replay_via_pipe

SCRIPTS_DIR = Path(__file__).parent / "scripts"

# Raw Python replay reads a single linear stream. Fork/exec/subprocess traces
# contain multiple long per-process runs and are only replayable once the Go
# replay pipeline has split them appropriately. The socket makefile crash
# repro is self-contained and installs a second retrace system inside the
# script, so it is not a raw-pipe record/replay target.
RAW_PIPE_REPLAY_UNSUPPORTED = {
    "exec_replacement.py",
    "fork_child.py",
    "fork_tree.py",
    "multiprocess_values.py",
    "socket_makefile_replay_socket_dealloc_crash.py",
    "subprocess_echo.py",
    "subprocess_time.py",
}

script_params = []
for path in sorted(SCRIPTS_DIR.glob("*.py")):
    if path.name in RAW_PIPE_REPLAY_UNSUPPORTED:
        script_params.append(pytest.param(
            path,
            id=path.name,
            marks=pytest.mark.skip(
                reason="fork/exec/subprocess replay requires the Go replay pipeline"
            ),
        ))
    else:
        script_params.append(pytest.param(path, id=path.name))


@pytest.fixture
def pipedir():
    d = tempfile.mkdtemp(prefix="retrace_pipe_rr_")
    os.mkfifo(os.path.join(d, "trace.bin"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.parametrize("script", script_params)
def test_replay(pipedir, script):
    record_then_replay_via_pipe(os.path.join(pipedir, "trace.bin"), str(script))
