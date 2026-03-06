"""Trace file reader with PID filtering.

Reads PID-framed records of the form [pid:4][len:2][payload] from the trace
file and yields only those matching the target PID.
"""

from __future__ import annotations

import logging
import os
import struct
from typing import BinaryIO, Iterator

log = logging.getLogger(__name__)

HEADER_FMT = "<IH"
HEADER_SIZE = struct.calcsize(HEADER_FMT)  # 6 bytes: 4 (pid) + 2 (len)


class TraceReader:
    """Sequential reader over a PID-framed trace file."""

    def __init__(self, path: str, target_pid: int) -> None:
        self.path = path
        self.target_pid = target_pid
        self._fd: BinaryIO | None = None
        self._offset: int = 0

    def open(self) -> None:
        self._fd = open(self.path, "rb")
        self._offset = 0
        log.info("Opened trace %s for pid %d", self.path, self.target_pid)

    def close(self) -> None:
        if self._fd is not None:
            self._fd.close()
            self._fd = None

    @property
    def offset(self) -> int:
        return self._offset

    def reopen_at(self, offset: int) -> None:
        """Close and re-open with an independent file description at *offset*.

        Used after fork() — the child must get its own file description so
        reads don't advance the parent's position.
        """
        if self._fd is not None:
            self._fd.close()
        self._fd = open(self.path, "rb")
        self._fd.seek(offset)
        self._offset = offset
        log.info("Re-opened trace at offset %d", offset)

    def records(self) -> Iterator[bytes]:
        """Yield payload bytes for records matching target_pid."""
        assert self._fd is not None, "call open() first"

        while True:
            header = self._fd.read(HEADER_SIZE)
            if len(header) < HEADER_SIZE:
                return

            pid, length = struct.unpack(HEADER_FMT, header)
            payload = self._fd.read(length)
            if len(payload) < length:
                return

            self._offset = self._fd.tell()

            if pid == self.target_pid:
                yield payload

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()
