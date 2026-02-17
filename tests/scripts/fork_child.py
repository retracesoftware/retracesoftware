"""Script that forks a child process using os.fork.

Parent prints 'parent:<pid>', child prints 'child:<pid>', both to stdout.
"""
import os
import sys

pid = os.fork()

if pid == 0:
    # child
    print(f"child:{os.getpid()}", flush=True)
    os._exit(0)
else:
    # parent
    os.waitpid(pid, 0)
    print(f"parent:{os.getpid()}", flush=True)
