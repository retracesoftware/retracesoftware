from __future__ import annotations

import queue
from collections import deque

from retracesoftware.install import install_retrace
from retracesoftware.proxy.io import recorder
from retracesoftware.proxy.system import disabled_callable


def test_sync_annotations_write_tape_with_retrace_disabled():
    """SYNC tape writes do not recursively trip patched synchronization APIs."""

    tape = []
    sync_queue = queue.SimpleQueue()

    def writer(*values):
        sync_queue.put(values)
        tape.extend(values)

    system = recorder(writer=writer)
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        items = queue.SimpleQueue()
        tape.clear()
        while True:
            try:
                sync_queue.get_nowait()
            except queue.Empty:
                break

        assert isinstance(system.sync, disabled_callable)
        system.run(items.put, "value")
        assert tape.count("SYNC") == 1

        queued_writes = []
        while True:
            try:
                queued_writes.append(sync_queue.get_nowait())
            except queue.Empty:
                break
        assert queued_writes.count(("SYNC",)) == 1
    finally:
        uninstall()


def test_plain_deque_queue_ops_do_not_emit_sync():
    """Plain deque operations are too broad to be global sync boundaries."""

    tape = []

    def writer(*values):
        tape.extend(values)

    system = recorder(writer=writer)
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        items = deque(["right", "left"])

        for operation, args in (
            (items.append, ("new-right",)),
            (items.appendleft, ("new-left",)),
            (items.pop, ()),
            (items.popleft, ()),
        ):
            tape.clear()
            system.run(operation, *args)
            assert "SYNC" not in tape
    finally:
        uninstall()


def test_queue_simplequeue_enqueue_ops_emit_sync():
    """Native SimpleQueue enqueue operations are synchronization boundaries."""

    tape = []

    def writer(*values):
        tape.extend(values)

    system = recorder(writer=writer)
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        items = queue.SimpleQueue()

        for operation, args in (
            (items.put, ("new-right",)),
            (items.put_nowait, ("new-nowait",)),
        ):
            tape.clear()
            system.run(operation, *args)
            assert "SYNC" in tape
    finally:
        uninstall()


def test_queue_simplequeue_dequeue_ops_do_not_emit_sync():
    """SimpleQueue dequeue waits are consumers of ordering, not wakeup edges."""

    tape = []

    def writer(*values):
        tape.extend(values)

    system = recorder(writer=writer)
    uninstall = install_retrace(system=system, retrace_shutdown=False)
    try:
        items = queue.SimpleQueue()
        items.put("existing")
        items.put("existing-nowait")

        for operation, args in (
            (items.get, ()),
            (items.get_nowait, ()),
        ):
            tape.clear()
            system.run(operation, *args)
            assert "SYNC" not in tape
    finally:
        uninstall()
