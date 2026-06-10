"""Unit coverage for passthrough fd provenance bookkeeping."""

from __future__ import annotations

import os
from pathlib import Path

from retracesoftware.install.fdprovenance import FdProvenance


def test_file_object_owner_keeps_fd_passthrough_only_while_alive(tmp_path: Path) -> None:
    provenance = FdProvenance()
    path = tmp_path / "passthrough.txt"

    handle = path.open("wb")
    fd = handle.fileno()
    provenance.mark_result(handle)

    assert provenance.should_retrace_fd(fd) is False

    handle.close()

    assert provenance.should_retrace_fd(fd) is True


def test_closed_file_object_fd_does_not_poison_reused_fd(tmp_path: Path) -> None:
    provenance = FdProvenance()
    path = tmp_path / "stale-owner.txt"

    with path.open("wb") as handle:
        stale_fd = handle.fileno()
        provenance.mark_result(handle)
        assert provenance.should_retrace_fd(stale_fd) is False

    read_fd, write_fd = os.pipe()
    try:
        assert read_fd == stale_fd or write_fd == stale_fd
        assert provenance.should_retrace_fd(stale_fd) is True
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_raw_fd_passthrough_propagates_through_dup2_and_clears_on_close(
    tmp_path: Path,
) -> None:
    provenance = FdProvenance()
    source_fd = os.open(tmp_path / "raw-fd.txt", os.O_CREAT | os.O_RDWR, 0o600)
    target_fd = None
    try:
        provenance.mark_result(source_fd)
        assert provenance.should_retrace_fd(source_fd) is False

        target_fd = os.dup(source_fd)
        provenance.observe_passthrough_call(
            "posix",
            "dup2",
            (source_fd, target_fd),
            {},
            target_fd,
        )
        assert provenance.should_retrace_fd(target_fd) is False

        provenance.observe_passthrough_call("posix", "close", (target_fd,), {}, None)
        os.close(target_fd)
        target_fd = None
        assert provenance.should_retrace_fd(source_fd) is False
        assert provenance.should_retrace_fd(source_fd + 1000) is True
    finally:
        if target_fd is not None:
            os.close(target_fd)
        os.close(source_fd)

