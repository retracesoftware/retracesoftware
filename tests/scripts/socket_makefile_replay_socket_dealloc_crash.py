"""Standalone repro for replay socket dealloc crash.

This reduces the failing in-process makefile replay teardown to the native
trigger we observed while debugging:

1. Record a tiny ``socketpair()`` + ``makefile("rb")`` interaction.
2. Replay it with retrace still installed.
3. Remove replay binding references while keeping temporary Python refs alive.
4. Release those refs one by one until the final closed replay socket dies.

On the current failing build, releasing the last replay-bound closed socket
segfaults in the native dealloc bridge path.
"""

from __future__ import annotations

import gc
import os
import socket
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]


def _bootstrap_local_checkout() -> None:
    build_tag = (
        f"cp{sys.version_info.major}{sys.version_info.minor}"
        f"{getattr(sys, 'abiflags', '')}"
    )

    entries = [
        str(_ROOT / "src"),
        str(_ROOT),
    ]
    for rel in (
        f"build/{build_tag}/cpp/cursor",
        f"build/{build_tag}/cpp/utils",
        f"build/{build_tag}/cpp/functional",
        f"build/{build_tag}/cpp/stream",
    ):
        path = _ROOT / rel
        if path.exists():
            entries.append(str(path))

    for entry in reversed(entries):
        if entry not in sys.path:
            sys.path.insert(0, entry)

    build_root = _ROOT / "build" / build_tag
    if build_root.exists():
        os.environ.setdefault("MESONPY_EDITABLE_SKIP", str(build_root))


_bootstrap_local_checkout()

from retracesoftware.install import ReplayDivergence, install_retrace
from retracesoftware.proxy.io import _ReplayBindingState, recorder, replayer
from retracesoftware.proxy.system import LifecycleHooks
from retracesoftware.testing.memorytape import _IOThreadAwareTapeWriter, _thread_id_context
from retracesoftware.threadid import ThreadId


IMMUTABLE_TYPES = {int, float, str, bytes, bool, type, type(None)}


def _work(_system):
    left, right = socket.socketpair()
    rfile = None
    try:
        rfile = left.makefile("rb", buffering=8192)
        return type(rfile).__name__
    finally:
        if rfile is not None:
            rfile.close()
        left.close()
        right.close()


def _build_recording():
    record_thread_ids = ThreadId()
    tape_storage: list[object] = []
    writer = _IOThreadAwareTapeWriter(tape_storage, thread=record_thread_ids.id.get)
    record_system = recorder(writer=writer.write, debug=False, stacktraces=False)
    record_system.immutable_types.update(IMMUTABLE_TYPES)

    with _thread_id_context(record_thread_ids):
        uninstall = install_retrace(
            system=record_system,
            monitor_level=0,
            verbose=False,
            retrace_shutdown=False,
        )
        try:
            recorded = record_system.run(lambda: _work(record_system))
        finally:
            uninstall()

    gc.collect()
    return tape_storage, recorded


def _make_replay_system(tape_storage):
    replay_iter = iter(tape_storage)
    replay_system = replayer(
        next_object=replay_iter.__next__,
        close=lambda: None,
        on_unexpected=lambda key: (_ for _ in ()).throw(
            ReplayDivergence(f"unexpected {key!r}", tape=list(tape_storage))
        ),
        on_desync=lambda record, replay: (_ for _ in ()).throw(
            ReplayDivergence(f"desync {record!r} vs {replay!r}", tape=list(tape_storage))
        ),
        debug=False,
        stacktraces=False,
    )
    replay_system.immutable_types.update(IMMUTABLE_TYPES)

    tape_reader = None
    for cell in replay_system.lifecycle_hooks.on_end.__closure__ or ():
        try:
            value = cell.cell_contents
        except ValueError:
            continue
        if isinstance(value, _ReplayBindingState):
            tape_reader = value
            break

    if tape_reader is None:
        raise RuntimeError("failed to locate replay binding state in replay on_end closure")

    # Disable the normal on_end drain so we can trigger the crash manually.
    replay_system.lifecycle_hooks = LifecycleHooks(
        on_start=replay_system.lifecycle_hooks.on_start,
        on_end=lambda: None,
    )
    return replay_system, tape_reader


def main():
    tape_storage, recorded = _build_recording()
    print(f"recorded={recorded}", flush=True)

    replay_system, tape_reader = _make_replay_system(tape_storage)

    with _thread_id_context(ThreadId()):
        uninstall = install_retrace(
            system=replay_system,
            monitor_level=0,
            verbose=False,
            retrace_shutdown=False,
        )
        try:
            replayed = replay_system.run(lambda: _work(replay_system))
            print(f"replayed={replayed}", flush=True)

            held = []
            for handle in list(tape_reader._bindings):
                value = tape_reader._bindings.pop(handle)
                if isinstance(value, socket.socket):
                    held.append((handle, value))

            print(f"sockets_retained={len(held)}", flush=True)

            for index, (handle, value) in enumerate(held):
                print(
                    f"release {index} handle={handle} type={type(value).__name__} repr={repr(value)[:120]}",
                    flush=True,
                )
                held[index] = (handle, None)

            print("released_all", flush=True)
            gc.collect()
        finally:
            uninstall()


if __name__ == "__main__":
    main()
