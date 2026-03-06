"""Retrace DAP debug adapter.

The adapter is used during replay: ``replay()`` in ``__main__.py``
connects a DAP socket to the Go replay proxy, installs debugger hooks,
and runs the DAP message loop in a background thread while the replay
executes on the main thread.
"""
