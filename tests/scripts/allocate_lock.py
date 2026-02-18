"""Investigate _thread.allocate_lock proxying."""
import _thread
import threading

# What does the _thread module namespace hold?
print("_thread.allocate_lock:", type(_thread.allocate_lock).__name__)

# What does threading's internal reference hold?
# threading caches _allocate_lock = _thread.allocate_lock at import time
_alloc = getattr(threading, '_allocate_lock', None)
print("threading._allocate_lock:", type(_alloc).__name__ if _alloc else "NOT FOUND")

# Are they the same object?
print("same?", _thread.allocate_lock is _alloc)

# Call both and check the result type
lock1 = _thread.allocate_lock()
print("_thread.allocate_lock() ->", type(lock1).__name__, type(lock1).__mro__)

if _alloc:
    lock2 = _alloc()
    print("threading._allocate_lock() ->", type(lock2).__name__, type(lock2).__mro__)

# Check if acquire works
print("lock1.acquire():", lock1.acquire())
lock1.release()
print("lock1.release(): ok")
