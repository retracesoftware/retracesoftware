"""Fork management for replay branching.

os.fork() creates a child replay process.  The child must:
1. Close the inherited trace file descriptor.
2. Re-open the trace file to get an independent file description.
3. Seek to the saved byte offset.
4. Connect to the DAP socket with its fork_id.

This avoids the shared-file-offset problem where parent and child reads
would advance each other's position.
"""

from __future__ import annotations

import logging
import os
import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .transport import TraceReader

log = logging.getLogger(__name__)


def fork_replay(
    trace: TraceReader,
    fork_id: str,
    dap_socket_path: str,
) -> int:
    """Fork the current replay process.

    Returns the child PID in the parent (> 0), or 0 in the child.
    The child re-opens the trace file and connects to the DAP socket.
    """
    saved_offset = trace.offset

    pid = os.fork()

    if pid > 0:
        log.info("Forked child pid=%d fork_id=%s", pid, fork_id)
        return pid

    # -- child process --
    trace.reopen_at(saved_offset)

    log.info("Child fork_id=%s connecting to %s", fork_id, dap_socket_path)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(dap_socket_path)

    rfile = sock.makefile("rb")
    wfile = sock.makefile("wb")

    from ..adapter import Adapter
    adapter = Adapter(rfile, wfile)
    adapter.cursor.counter = trace.offset
    try:
        adapter.run()
    finally:
        rfile.close()
        wfile.close()
        sock.close()

    os._exit(0)
    return 0  # unreachable, satisfies type checker
