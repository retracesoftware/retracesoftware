import threading

RESULTS = {}

def worker(n):
    total = 0
    for i in range(n):
        total += i
    RESULTS[threading.current_thread().name] = total

threads = []
for i in range(3):
    t = threading.Thread(target=worker, args=(10 + i,), name=f"worker-{i}")
    threads.append(t)
    t.start()

for t in threads:
    t.join()

print(RESULTS)
