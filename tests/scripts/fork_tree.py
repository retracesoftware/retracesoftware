"""Fork tree: fork 3 times, each process prints its binary path.

At each fork point, parent appends '0' and child appends '1' to the
path string.  This creates 2^3 = 8 leaf processes, each printing a
unique 3-character path like 'path:010'.
"""
import os

N = 3
path = ""

for i in range(N):
    pid = os.fork()
    if pid == 0:
        path += "1"
    else:
        os.waitpid(pid, 0)
        path += "0"

print(f"path:{path}", flush=True)
