"""Regression: fd-backed `_io.open()` results must compose with TextIOWrapper."""

from __future__ import annotations

import os
from pathlib import Path

from tests.helpers import run_record


def test_record_textiowrapper_over_fd_open_preserves_buffer_shape(tmp_path: Path):
    script = tmp_path / "io_fd_textiowrapper.py"
    script.write_text(
        (
            "import _io\n"
            "import os\n"
            "\n"
            "r, w = os.pipe()\n"
            "os.write(w, b'hello\\n')\n"
            "os.close(w)\n"
            "\n"
            "buf = _io.open(r, 'rb')\n"
            "wrapper = _io.TextIOWrapper(buf)\n"
            "print(wrapper.read(), end='', flush=True)\n"
        ),
        encoding="utf-8",
    )

    recording = tmp_path / "trace.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"

    record = run_record(str(script), str(recording), env=env)

    assert record.returncode == 0, (
        "record failed for fd-backed _io.open() handed to TextIOWrapper\n"
        f"exit: {record.returncode}\n"
        f"stdout:\n{record.stdout}\n"
        f"stderr:\n{record.stderr}"
    )
    assert record.stdout == "hello\n"
