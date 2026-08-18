import os
import subprocess
from importlib.metadata import requires
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RETRACE_TEST_INSTALLED_WHEEL") != "1",
    reason="installed-wheel packaging smoke",
)


def test_installed_wheel_uses_retrace_dap_replay_binary():
    assert "RETRACE_REPLAY_BIN" not in os.environ

    from retracesoftware.replay import binary_path

    path = Path(binary_path())
    assert path.name == "replay"
    assert path.parent.name == "bin"
    assert path.parent.parent.name == "retrace_dap"
    assert path.is_file()
    assert os.access(path, os.X_OK)

    proc = subprocess.run(
        [str(path), "--help"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0
    assert "replay - retrace recording and PidFile tool" in proc.stdout + proc.stderr


def test_installed_wheel_declares_retrace_dap_dependency():
    package_requires = requires("retracesoftware") or []

    assert any(
        requirement == "retracesoftware-dap==0.2.26"
        for requirement in package_requires
    )
