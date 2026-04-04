"""Base mode abstraction for installing retrace handlers."""

from abc import ABC, abstractmethod

from retracesoftware.proxy.context import Context, LifecycleHooks

class Mode(ABC):
    """Abstract record/replay policy installed onto a ``System``."""

    def __init__(self, system, *, on_start=None, on_end=None):
        self.system = system
        self.on_start = on_start
        self.on_end = on_end

    def _create_context(self, **kwargs):
        return Context(
            self.system,
            lifecycle_hooks=LifecycleHooks(
                on_start=self.on_start,
                on_end=self.on_end,
            ),
            **kwargs,
        )

    @abstractmethod
    def context(self):
        """Build the installed-handler context manager for this mode."""
