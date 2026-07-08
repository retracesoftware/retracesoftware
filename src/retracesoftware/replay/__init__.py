from __future__ import annotations

import os
import sys


def binary_path() -> str:
    """Return the packaged replay/DAP binary path.

    The binary is provided by the ``retracesoftware-dap`` distribution.
    ``RETRACE_REPLAY_BIN`` is still supported by that package for local
    development and unusual deployments.
    """

    env_bin = os.environ.get("RETRACE_REPLAY_BIN")
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin

    try:
        from retrace_dap import binary_path as _dap_binary_path
    except ImportError as exc:
        raise FileNotFoundError(
            "Retrace replay/DAP binary package is not installed. Install "
            "retracesoftware-dap or set RETRACE_REPLAY_BIN."
        ) from exc

    return _dap_binary_path()


def extract_binary_path() -> str:
    """Return the path to the binary for extraction (unified into replay)."""

    return binary_path()


def _exec_replay() -> None:
    """Find the replay/DAP binary and exec it."""

    replay = binary_path()
    os.execvp(replay, [replay] + sys.argv[1:])
