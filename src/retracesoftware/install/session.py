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
    - bind canonical wrapped-function callback identities once active
    """

    __slots__ = ("_wrapped_attrs", "_wrapped_by_target", "_bound_keys", "_bind")

    def __init__(self):
        self._wrapped_attrs = {}
        self._wrapped_by_target = {}
        self._bound_keys = set()
        self._bind = None

    @staticmethod
    def _key(owner, name):
        return (owner.__module__, owner.__qualname__, name)

    @staticmethod
    def _binding_identity(entry):
        if isinstance(entry.wrapped, utils.wrapped_function):
            return entry.wrapped
        return None

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
        identity = self._binding_identity(entry)
        if identity is not None:
            self._bind(identity)
        self._bound_keys.add(key)

    def activate_callback_binding(self, bind):
        self._bind = bind
        self._bound_keys.clear()
        self.bind_callback_targets(bind)

    def deactivate_callback_binding(self):
        self._bind = None
        self._bound_keys.clear()

    def callback_binding_hooks(self, bind):
        return {
            "on_start": lambda: self.activate_callback_binding(bind),
            "on_end": self.deactivate_callback_binding,
        }

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
