"""Minimal repro: io.open returns a BufferedReader, then TextIOWrapper wraps it.

During recording, io.open's return value is wrapped in a DynamicProxy by the
proxy_output step.  TextIOWrapper.__init__ accesses the BufferedReader's C
struct internals directly (not through Python attribute lookup), so the proxy
is opaque to it and it raises ValueError: I/O operation on uninitialized object.
"""
import io
import os

r, w = os.pipe()
os.write(w, b"hello\n")
os.close(w)

buf = io.open(r, "rb")
wrapper = io.TextIOWrapper(buf)
print(wrapper.read(), end="", flush=True)
