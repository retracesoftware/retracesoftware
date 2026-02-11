import hashlib
import os
import queue
import threading
from pathlib import Path

WORK_ITEMS = 2000
WORKERS = int(os.getenv("THREAD_WORKERS", "24"))
# The test directory is mounted read-only in the harness, so write logs under /tmp.
OUT_DIR = Path(os.getenv("THREAD_OUT_DIR", "/tmp/retrace_threading_stress"))
OUT_FILE = OUT_DIR / "thread_log.txt"


class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.counter = 0
        self.ready_workers = 0
        self.started = False
        self.total_written = 0


def stable_payload(i: int) -> str:
    # deterministic payload, but enough entropy for stress
    h = hashlib.sha256(f"item:{i}".encode()).hexdigest()
    return f"{i}:{h}\n"


def worker(worker_id: int, q: queue.Queue, state: SharedState, start_event: threading.Event):
    # signal ready + barrier-like start using Condition
    with state.cond:
        state.ready_workers += 1
        state.cond.notify_all()

    start_event.wait()

    local_written = 0
    local_hash = hashlib.sha256()

    while True:
        item = q.get()
        if item is None:
            q.task_done()
            break

        payload = stable_payload(item)
        # simulate small CPU work to amplify scheduling interleavings
        digest = hashlib.sha256(payload.encode()).digest()
        local_hash.update(digest)

        # critical section contended by all workers
        with state.lock:
            state.counter += 1

        # file append (shared file) — serialize writes so output is deterministic
        # We intentionally do NOT rely on OS atomicity of append across threads.
        with state.lock:
            with OUT_FILE.open("a", encoding="utf-8") as f:
                f.write(f"W{worker_id} " + payload)
                state.total_written += 1
                local_written += 1

        q.task_done()

    # store thread summary deterministically
    with state.lock:
        with OUT_FILE.open("a", encoding="utf-8") as f:
            f.write(f"SUMMARY W{worker_id} written={local_written} hash={local_hash.hexdigest()}\n")


def main():
    print("=" * 70)
    print("threading_stress_test: threads + locks + condvars + queue + file I/O")
    print("=" * 70)

    OUT_DIR.mkdir(exist_ok=True)
    if OUT_FILE.exists():
        OUT_FILE.unlink()

    q = queue.Queue()
    state = SharedState()

    start_event = threading.Event()
    threads = []

    for wid in range(WORKERS):
        t = threading.Thread(target=worker, args=(wid, q, state, start_event), daemon=True)
        t.start()
        threads.append(t)

    # wait until all workers are ready (condvar)
    with state.cond:
        while state.ready_workers < WORKERS:
            state.cond.wait(timeout=0.1)

    # enqueue work
    for i in range(WORK_ITEMS):
        q.put(i)

    # start all workers at once
    start_event.set()

    # stop tokens
    for _ in range(WORKERS):
        q.put(None)

    q.join()

    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "Thread failed to terminate"

    # Validate invariants
    assert state.counter == WORK_ITEMS, f"Expected counter={WORK_ITEMS}, got {state.counter}"
    assert state.total_written == WORK_ITEMS, f"Expected total_written={WORK_ITEMS}, got {state.total_written}"

    # Validate output is stable via global hash (should match on replay)
    data = OUT_FILE.read_bytes()
    out_hash = hashlib.sha256(data).hexdigest()
    print(f"✓ wrote {state.total_written} items with output hash={out_hash}")

    # Basic sanity: file must contain the expected number of "W" lines
    line_count = data.count(b"\n")
    assert line_count >= WORK_ITEMS, "Output file unexpectedly short"

    print("✓ threading stress test passed")


if __name__ == "__main__":
    main()
