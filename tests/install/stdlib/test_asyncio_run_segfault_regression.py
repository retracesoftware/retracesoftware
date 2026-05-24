"""Regression: `_socket` proxying (`socket` + `socketpair`) segfaults asyncio record.

Root component identified by bisect:
- Removing `_socket` config from install patching avoids the crash.
- Keeping only `_socket` proxy = ["socket", "socketpair"] reproduces it.

So this test isolates to a single retrace component path:
`install` module patching for stdlib `_socket`.
"""

from __future__ import annotations

import os
import importlib
from pathlib import Path
import socket
import _socket
import subprocess

import pytest

from tests.helpers import PYTHON
from retracesoftware.install import _reload_preexisting_subclass_modules
from retracesoftware.install.installation import Installation
from retracesoftware.install.patcher import patch
from retracesoftware.proxy.system import System


class _Writer:
    def __init__(self):
        self.calls = []

    def callback(self, fn, args, kwargs):
        self.calls.append(("callback", fn, args, kwargs))

    def error(self, error):
        self.calls.append(("error", error))

    def result(self, value):
        self.calls.append(("result", value))

    def thread_switch(self, cursor_delta, thread_id):
        self.calls.append(("thread_switch", cursor_delta, thread_id))

    def checkpoint(self, cursor_delta, thread_id, value):
        self.calls.append(("checkpoint", cursor_delta, thread_id, value))

    def binding_delete(self, binding):
        self.calls.append(("binding_delete", binding))


def test_socket_socketpair_records_after_only_socket_config_is_installed():
    writer = _Writer()
    system = System.record_system(writer=writer, debug=False)
    system.add_immutable_types(int, socket.AddressFamily, socket.SocketKind)
    installation = Installation(system)
    undo = patch(
        _socket,
        {"proxy": ["socket", "socketpair"]},
        installation,
        update_refs=False,
    )
    _reload_preexisting_subclass_modules(
        installation,
        disable_for=system.disable_for,
    )

    try:
        left, right = system.run_internal(socket.socketpair)
        try:
            assert isinstance(left, socket.socket)
            assert isinstance(right, socket.socket)
        finally:
            left.close()
            right.close()
    finally:
        undo()
        importlib.reload(socket)


@pytest.mark.parametrize("config_name", ["release", "debug"])
def test_record_minimal_asyncio_run_does_not_segfault(tmp_path: Path, config_name: str):
    script = tmp_path / "mini_asyncio.py"
    script.write_text(
        (
            "import asyncio\n"
            "async def main():\n"
            "    await asyncio.sleep(0.01)\n"
            "asyncio.run(main())\n"
            "print('ok')\n"
        ),
        encoding="utf-8",
    )

    # Isolate to just the `_socket` install config that reproduces the crash.
    modules_dir = tmp_path / "modules"
    modules_dir.mkdir()
    (modules_dir / "_socket.toml").write_text(
        'proxy = ["socket", "socketpair"]\n',
        encoding="utf-8",
    )

    recording = tmp_path / f"{config_name}.retrace"
    env = os.environ.copy()
    env["PYTHONFAULTHANDLER"] = "1"
    env["RETRACE_CONFIG"] = config_name
    env["RETRACE_MODULES_PATH"] = str(modules_dir)

    proc = subprocess.run(
        [
            PYTHON,
            "-m",
            "retracesoftware",
            "--recording",
            str(recording),
            "--format",
            "unframed_binary",
            "--",
            str(script),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 0, (
        f"record failed for config={config_name} (exit {proc.returncode})\n"
        f"stdout:\n{proc.stdout}\n"
        f"stderr:\n{proc.stderr}"
    )
