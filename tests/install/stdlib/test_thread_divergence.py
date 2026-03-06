"""Demonstrates _thread lock divergence under proxying.

When ``_thread.RLock`` is type-patched, every acquire/release becomes
a recorded event.  Any shared mutable state that changes between
record and replay causes the lock-operation count to diverge,
producing a ``ReplayDivergence`` (or a hang in the demux, before the
demux timeout was added).

This is the exact pattern that breaks urllib3: its connection pool
keeps connections alive across record/replay, so the pool-lock
sequence differs on the second pass.

``_thread`` is patched by conftest via TOML (``proxy = ["allocate",
"allocate_lock", "RLock"]``).  These tests exercise the divergence
that results from shared mutable state persisting between the record
and replay phases.
"""
import _thread
import pytest

from retracesoftware.install import ReplayDivergence


# ── tests ─────────────────────────────────────────────────────────

def test_rlock_shared_state_divergence(system, runner):
    """Shared mutable state causes different RLock operation counts.

    Record: state is empty → takes the initialisation branch
            (2 acquire + 2 release via reentrant locking).
    Replay: state was mutated during record → takes the fast path
            (1 acquire + 1 release).
    Result: tape has 4 lock events but replay only consumes 2 →
            subsequent reads are misaligned → ReplayDivergence.

    This is exactly what happens with urllib3's PoolManager: on
    the first pass the connection pool is empty and creates a new
    connection (extra lock churn); on replay the pool already holds
    the connection from record and skips creation.
    """
    state = {}

    def work():
        lock = _thread.RLock()
        if 'init' not in state:
            lock.acquire()
            lock.acquire()          # reentrant
            state['init'] = True
            lock.release()
            lock.release()
        else:
            lock.acquire()
            lock.release()
        return 42

    with pytest.raises(ReplayDivergence):
        runner.run(work, timeout=3)


def test_allocate_lock_divergence(system, runner):
    """Different number of lock *creations* between record and replay.

    Record: state is empty → allocate_lock() called to create a lock.
    Replay: state already has the lock → allocate_lock() skipped.
    Result: tape has an extra (SYNC, RESULT) pair from the factory
            call → all subsequent events misalign.

    This mirrors lazy initialisation patterns in library code where
    a lock is created on first use and cached thereafter.
    """
    cache = {}

    def work():
        if 'lock' not in cache:
            cache['lock'] = _thread.allocate_lock()
        cache['lock'].acquire()
        cache['lock'].release()
        return 1

    with pytest.raises(ReplayDivergence):
        runner.run(work, timeout=3)
