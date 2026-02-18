"""End-to-end record/replay through a named pipe for each script in scripts/."""
import os
import tempfile
import shutil
from pathlib import Path

import pytest

from run_record_replay import record_then_replay_via_pipe

SCRIPTS_DIR = Path(__file__).parent / "scripts"

script_files = sorted(SCRIPTS_DIR.glob("*.py"))
script_ids = [p.name for p in script_files]


@pytest.fixture
def pipedir():
    d = tempfile.mkdtemp(prefix="retrace_pipe_rr_")
    os.mkfifo(os.path.join(d, "trace.bin"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.mark.parametrize("script", script_files, ids=script_ids)
def test_replay(pipedir, script):
    record_then_replay_via_pipe(pipedir, str(script))
