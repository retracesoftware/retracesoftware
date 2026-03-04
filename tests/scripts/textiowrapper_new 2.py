"""Minimal repro: just allocating a TextIOWrapper fails under recording.

TextIOWrapper.__new__(TextIOWrapper) triggers the _on_alloc hook which
calls ext_bind on the uninitialized instance.  The C++ writer's ext_bind
tries to format an error message using %S (PyObject_Str) on the
uninitialized object, which hits CHECK_INITIALIZED and raises
ValueError: I/O operation on uninitialized object.
"""
import io
tw = io.TextIOWrapper.__new__(io.TextIOWrapper)
print("ok")
