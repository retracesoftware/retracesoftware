"""Install-time session state for module patching and callback identity."""

from dataclasses import dataclass

import retracesoftware.utils as utils


@dataclass(frozen=True)
class _WrappedAttr:
    owner: type
    name: str
    target: object
    wrapped: object


class InstallSession:
    """Own install-time patch metadata that should not live on ``System``.

    The session tracks wrapped class attributes created during module patching
    so we can:

    - bind their canonical callback identities once record/replay is active
    - normalize recorded callback ``fn`` payloads into a stable representation
    - bind descriptor callback callables for wrapped members
    """

    __slots__ = ("_wrapped_attrs", "_wrapped_by_target", "_bound_keys", "_bound_callables", "_bind")

    def __init__(self):
        self._wrapped_attrs = {}
        self._wrapped_by_target = {}
        self._bound_keys = set()
        self._bound_callables = set()
        self._bind = None

    @staticmethod
    def _key(owner, name):
        return (owner.__module__, owner.__qualname__, name)

    @staticmethod
    def _binding_identity(entry):
        if isinstance(entry.wrapped, utils.wrapped_function):
            return entry.wrapped
        return entry.target

    @staticmethod
    def _descriptor_callbacks(entry):
        if not isinstance(entry.wrapped, utils.wrapped_member):
            return ()

        callbacks = []
        descriptor_type = type(entry.target)
        for name in ("__get__", "__set__", "__delete__"):
            callback = getattr(descriptor_type, name, None)
            if callback is not None:
                callbacks.append(callback)
        return tuple(callbacks)

    def register_wrapped_attr(self, owner, name, target, wrapped):
        key = self._key(owner, name)
        self._wrapped_attrs[key] = _WrappedAttr(
            owner=owner,
            name=name,
            target=target,
            wrapped=wrapped,
        )
        self._wrapped_by_target[target] = wrapped

        if self._bind is not None and key not in self._bound_keys:
            self._bind_entry(self._wrapped_attrs[key], key)

    def _bind_entry(self, entry, key):
        self._bind(self._binding_identity(entry))
        self._bound_keys.add(key)
        for callback in self._descriptor_callbacks(entry):
            if callback in self._bound_callables:
                continue
            self._bind(callback)
            self._bound_callables.add(callback)

    def activate_callback_binding(self, bind):
        self._bind = bind
        self.bind_callback_targets(bind)

    def deactivate_callback_binding(self):
        self._bind = None

    def bind_callback_targets(self, bind=None):
        bind = self._bind if bind is None else bind
        if bind is None:
            return

        for key in sorted(self._wrapped_attrs):
            if key in self._bound_keys:
                continue
            self._bind_entry(self._wrapped_attrs[key], key)

    def normalize_record_callback(self, fn):
        wrapped = self._wrapped_by_target.get(fn)
        if wrapped is not None and isinstance(wrapped, utils.wrapped_function):
            return wrapped

        return fn

    def normalize_replay_callback(self, fn):
        return fn
