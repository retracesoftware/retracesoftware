"""Context installation helpers for the gate-based proxy system."""

import _thread
from typing import Any, Callable, NamedTuple, TypeAlias

Executor: TypeAlias = Callable[..., Any]
BindHandler: TypeAlias = Callable[[object], Any]
LifecycleHook: TypeAlias = Callable[[], Any]


class Handler(NamedTuple):
    """Frozen gate handler configuration."""

    executor: Executor


class _GateContext:
    """Reusable, thread-safe context manager for handler installation."""

    __slots__ = (
        "_system",
        "_internal",
        "_external",
        "_bind",
        "_async_new_patched",
        "_on_start",
        "_on_end",
        "_saved",
    )

    def __init__(
        self,
        system,
        *,
        internal: Handler | None = None,
        external: Handler | None = None,
        bind: BindHandler | None = None,
        async_new_patched: BindHandler | None = None,
        on_start: LifecycleHook | None = None,
        on_end: LifecycleHook | None = None,
    ) -> None:
        self._system = system
        self._internal = internal
        self._external = external
        self._bind = bind
        self._async_new_patched = async_new_patched
        self._on_start = on_start
        self._on_end = on_end
        self._saved = _thread._local()

    def _install(self) -> LifecycleHook:
        saved = {}
        restored = False

        def maybe_save_handler(name: str, value: Handler | None) -> None:
            if value is None:
                return
            gate = getattr(self._system, f"_{name}")
            saved[name] = gate.executor
            gate.executor = value.executor

        def maybe_save_gate(name: str, value: BindHandler | None) -> None:
            if value is None:
                return
            gate = getattr(self._system, f"_{name}")
            saved[name] = gate.executor
            gate.executor = value

        maybe_save_handler("internal", self._internal)
        maybe_save_handler("external", self._external)
        maybe_save_gate("bind", self._bind)
        maybe_save_gate("async_new_patched", self._async_new_patched)

        def restore() -> None:
            nonlocal restored
            if restored:
                return
            restored = True

            for name in ("async_new_patched", "bind", "external", "internal"):
                if name in saved:
                    getattr(self._system, f"_{name}").executor = saved[name]

        try:
            if self._on_start is not None:
                self._on_start()
        except Exception:
            restore()
            raise

        def uninstall() -> None:
            try:
                if self._on_end is not None:
                    self._on_end()
            finally:
                restore()

        return uninstall

    def __enter__(self):
        current_context = self._system.current_context.context(self)
        current_context.__enter__()
        self._saved.current_context = current_context
        try:
            self._saved.uninstall = self._install()
        except Exception:
            current_context.__exit__(None, None, None)
            raise
        return self._system

    def __exit__(self, *exc) -> bool:
        try:
            self._saved.uninstall()
        finally:
            self._saved.current_context.__exit__(*exc)
        return False
