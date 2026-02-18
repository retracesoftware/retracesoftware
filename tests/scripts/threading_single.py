"""Minimal threading test — single thread start/join."""
import threading

result = []

def worker():
    result.append("worker ran")

t = threading.Thread(target=worker)
t.start()
t.join()

print(result[0])
