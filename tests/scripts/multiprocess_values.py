"""Script that spawns multiple subprocesses and collects their outputs.

Each child prints a value derived from time.time(); the parent collects
and prints them all as a sorted list.
"""
import subprocess
import sys
import json

CHILD_SCRIPT = '''
import time
import json
print(json.dumps({"t": time.time()}))
'''

results = []
for i in range(3):
    proc = subprocess.run(
        [sys.executable, "-c", CHILD_SCRIPT],
        capture_output=True, text=True,
    )
    data = json.loads(proc.stdout.strip())
    results.append(data['t'])

print(json.dumps(results), flush=True)
