"""Multithreaded scheduling test.

Spawns threads that each compute and print a result.
The output order depends on thread scheduling, so replay
must reproduce the exact same interleaving to match stdout.

Known issue: replay fails because threading.Thread.start()
internally calls self._started.wait(), which uses Condition/Lock
primitives. During replay, _allocate_lock() is proxied and returns
the replayed value (None) instead of an actual lock object, causing
AttributeError: 'NoneType' object has no attribute 'acquire'.
"""
import threading

results = []

def worker(tid):
    total = 0
    for i in range(1000):
        total += i * tid
    results.append(f"{tid}:{total}")

threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
for t in threads:
    t.start()
for t in threads:
    t.join()

for r in results:
    print(r)
