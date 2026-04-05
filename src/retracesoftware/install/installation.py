from dataclasses import dataclass

from retracesoftware.install.replace import update, update_module_refs
from retracesoftware.install.session import InstallSession


_MISSING = object()


@dataclass(frozen=True)
class InstalledObject:
    module_name: str | None
    name: str
    original: object
    current: object


@dataclass(frozen=True)
class _NamespaceChange:
    namespace: dict
    name: str
    old: object
    new: object
    update_refs: bool
    module_refs_only: bool


class Installation:
    """Track concrete install-time mutations and undo them later.

    The installation is intentionally dumb: callers decide *what* to patch or
    proxy, and this object just performs the mutation against a module
    namespace, records enough information to undo it, and delegates runtime
    behavior to the provided ``System``.
    """

    __slots__ = (
        "system",
        "install_session",
        "update_refs",
        "module_refs_only",
        "_changes",
        "_patched_types",
        "_module_objects",
        "_uninstalled",
    )

    def __init__(self, system, install_session=None, *, update_refs=False, module_refs_only=False):
        self.system = system
        self.install_session = InstallSession() if install_session is None else install_session
        self.update_refs = update_refs
        self.module_refs_only = module_refs_only
        self._changes = []
        self._patched_types = []
        self._module_objects = []
        self._uninstalled = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.uninstall()
        return False

    @property
    def module_objects(self):
        return tuple(self._module_objects)

    def _namespace(self, module):
        if isinstance(module, dict):
            return module

        namespace = getattr(module, "__dict__", None)
        if isinstance(namespace, dict):
            return namespace

        raise TypeError(f"expected module or namespace dict, got {type(module).__name__!r}")

    def _module_name(self, namespace):
        return namespace.get("__name__") if isinstance(namespace, dict) else None

    def _track_object(self, namespace, name, original, current):
        self._module_objects.append(
            InstalledObject(
                module_name=self._module_name(namespace),
                name=name,
                original=original,
                current=current,
            )
        )

    def _track_type(self, cls):
        if cls not in self._patched_types:
            self._patched_types.append(cls)

    def _resolve_update_refs(self, update_refs, module_refs_only):
        if update_refs is None:
            update_refs = self.update_refs
        if module_refs_only is None:
            module_refs_only = self.module_refs_only
        return update_refs, module_refs_only

    @staticmethod
    def _apply_ref_updates(old, new, *, module_refs_only):
        if module_refs_only:
            update_module_refs(old, new)
        else:
            update(old, new)

    def replace(self, module, name, new, *, update_refs=None, module_refs_only=None):
        namespace = self._namespace(module)
        old = namespace.get(name, _MISSING)
        if old is _MISSING:
            raise KeyError(name)
        update_refs, module_refs_only = self._resolve_update_refs(update_refs, module_refs_only)

        self._track_object(namespace, name, old, new)

        if old is new:
            return new

        namespace[name] = new
        self._changes.append(
            _NamespaceChange(
                namespace=namespace,
                name=name,
                old=old,
                new=new,
                update_refs=update_refs,
                module_refs_only=module_refs_only,
            )
        )

        if update_refs:
            self._apply_ref_updates(old, new, module_refs_only=module_refs_only)

        return new

    def patch_type(self, module, name):
        namespace = self._namespace(module)
        value = namespace.get(name, _MISSING)
        if value is _MISSING:
            raise KeyError(name)
        if not isinstance(value, type):
            raise TypeError(f"cannot patch_type {type(value).__name__!r} object")

        self.system.patch_type(value, install_session=self.install_session)
        self._track_type(value)
        self._track_object(namespace, name, value, value)
        return value

    def proxy(self, module, name, *, update_refs=None, module_refs_only=None):
        namespace = self._namespace(module)
        value = namespace.get(name, _MISSING)
        if value is _MISSING:
            raise KeyError(name)
        update_refs, module_refs_only = self._resolve_update_refs(update_refs, module_refs_only)

        new = self.system.patch(value, install_session=self.install_session)
        if isinstance(value, type):
            self._track_type(value)

        self._track_object(namespace, name, value, new)

        if new is not value:
            namespace[name] = new
            self._changes.append(
                _NamespaceChange(
                    namespace=namespace,
                    name=name,
                    old=value,
                    new=new,
                    update_refs=update_refs,
                    module_refs_only=module_refs_only,
                )
            )

            if update_refs:
                self._apply_ref_updates(value, new, module_refs_only=module_refs_only)

        return new

    def patch(self, module, name, *, update_refs=None, module_refs_only=None):
        return self.proxy(
            module,
            name,
            update_refs=update_refs,
            module_refs_only=module_refs_only,
        )

    def uninstall(self):
        if self._uninstalled:
            return

        for change in reversed(self._changes):
            change.namespace[change.name] = change.old
            if change.update_refs and change.old is not change.new:
                self._apply_ref_updates(
                    change.new,
                    change.old,
                    module_refs_only=change.module_refs_only,
                )

        for cls in sorted(self._patched_types, key=lambda cls: len(cls.__mro__), reverse=True):
            if getattr(cls, "__retrace_system__", None) is self.system:
                self.system.unpatch_type(cls)

        self._changes.clear()
        self._patched_types.clear()
        self._module_objects.clear()
        self._uninstalled = True
