"""Script that launches a Python subprocess which prints time.time().

If the child is recorded under retrace, its time.time() call will be
captured and replayed deterministically.  If the child is NOT recorded,
the replayed value will differ from the recorded value.
"""
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-c", "import time; print(time.time())"],
    capture_output=True, text=True,
)
print(f"child_time:{result.stdout.strip()}", flush=True)
