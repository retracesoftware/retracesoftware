"""Script that launches a subprocess via subprocess.run.

Runs `python -c 'print("hello from child")'` and prints the captured
output from the parent.
"""
import subprocess
import sys

result = subprocess.run(
    [sys.executable, "-c", 'print("hello from child")'],
    capture_output=True, text=True,
)
print(f"parent got: {result.stdout.strip()}", flush=True)
