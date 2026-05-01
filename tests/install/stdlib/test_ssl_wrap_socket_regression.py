"""Regression: direct install component must not crash on SSL wrap.

This is the most direct reproducer of the retrace component failure:
- Use retrace's in-process test runner directly
- Execute only stdlib ssl.wrap_socket (no handshake/network required)
- Process currently segfaults in ssl.py::_create under retrace patching
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_install_component_ssl_wrap_socket_does_not_crash(tmp_path):
    script = tmp_path / "ssl_wrap_socket_repro.py"
    script.write_text(
        textwrap.dedent(
            """
            import socket
            import ssl
            from tests.runner import Runner

            runner = Runner()

            def work():
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                ctx = ssl.create_default_context()
                wrapped = ctx.wrap_socket(
                    s,
                    server_hostname="example.com",
                    do_handshake_on_connect=False,
                )
                wrapped.close()
                s.close()

            runner.record(work)
            print("ok", flush=True)
            """
        ),
        encoding="utf-8",
    )

    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(repo_root)
        if not existing_pythonpath
        else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    )

    proc = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )

    assert proc.returncode == 0, (
        f"install component crashed (exit {proc.returncode}):\n"
        f"stdout: {proc.stdout}\n"
        f"stderr: {proc.stderr}"
    )
