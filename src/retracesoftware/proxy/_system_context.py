"""Context installation helpers for the gate-based proxy system."""

import _thread


class _GateContext:
    """Reusable, thread-safe context manager for gate executors."""

    __slots__ = ("_system", "_kwargs", "_saved")

    def __init__(self, system, **kwargs):
        self._system = system
        self._kwargs = kwargs
        self._saved = _thread._local()

    def __enter__(self):
        self._saved.current_context = self._system.current_context.context(self)
        self._saved.current_context.__enter__()

        saved = {}
        for key in self._kwargs:
            saved[key] = getattr(self._system, key).executor
        self._saved.state = saved

        for key, value in self._kwargs.items():
            setattr(getattr(self._system, key), "executor", value)
        return self._system

    def __exit__(self, *exc):
        for key, value in self._saved.state.items():
            setattr(getattr(self._system, key), "executor", value)
        self._saved.current_context.__exit__(*exc)
        return False
