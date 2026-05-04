import os
import sys


print(f"CHILD {sys.argv[1]} {os.environ['RETRACE_CHILD_MULTIPLIER']}")
print("ERR ok", file=sys.stderr)
