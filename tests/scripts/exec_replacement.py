"""Script that replaces itself via os.execv.

Calls os.execv to replace the current process with a Python one-liner
that prints a message.
"""
import os
import sys

os.execv(sys.executable, [sys.executable, "-c", 'print("exec replacement done")'])
