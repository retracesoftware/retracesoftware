"""Multithreaded scheduling test.

Spawns threads that each compute and print a result.
The output order depends on thread scheduling, so replay
must reproduce the exact same interleaving to match stdout.
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
