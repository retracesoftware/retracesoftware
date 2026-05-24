from dataclasses import dataclass

from retracesoftware.install.replace import restore_module_refs, update, update_module_refs
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
    module_ref_changes: tuple


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
        "_type_replacements",
        "_changes",
        "_module_objects",
        "_uninstalled",
    )

    def __init__(
        self,
        system,
        install_session=None,
        *,
        update_refs=False,
        module_refs_only=False,
    ):
        self.system = system
        self.install_session = InstallSession() if install_session is None else install_session
        self.update_refs = update_refs
        self.module_refs_only = module_refs_only
        self._type_replacements = []
        self._changes = []
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

    @property
    def type_replacements(self):
        return tuple(self._type_replacements)

    def record_type_replacement(self, old, new):
        if old is not new and isinstance(old, type) and isinstance(new, type):
            self._type_replacements.append((old, new))

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

    def bind(self, obj):
        return self.system.bind(obj)

    def add_immutable_type(self, cls):
        if not isinstance(cls, type):
            raise TypeError(f"cannot add immutable {type(cls).__name__!r} object")
        add = getattr(self.system, "add_immutable_type", None)
        if add is not None:
            add(cls)
        else:
            self.system.immutable_types.add(cls)
        return cls

    def patch_value(self, value):
        if isinstance(value, type):
            return self.system.proxy_type(value)

        if callable(value):
            patch_function = getattr(self.system, "patch_function", None)
            if patch_function is not None:
                return patch_function(value)

        raise TypeError(f"cannot patch {type(value).__name__!r} object")

    def _resolve_update_refs(self, update_refs, module_refs_only):
        if update_refs is None:
            update_refs = self.update_refs
        if module_refs_only is None:
            module_refs_only = self.module_refs_only
        return update_refs, module_refs_only

    @staticmethod
    def _apply_ref_updates(old, new, *, module_refs_only):
        # Module namespace aliases are a core install surface and must be
        # restored deterministically even when their dicts are GC-untracked.
        module_ref_changes = tuple(update_module_refs(old, new))
        if not module_refs_only:
            update(old, new)
        return module_ref_changes

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
        self.record_type_replacement(old, new)
        self._changes.append(
            _NamespaceChange(
                namespace=namespace,
                name=name,
                old=old,
                new=new,
                update_refs=update_refs,
                module_refs_only=module_refs_only,
                module_ref_changes=(),
            )
        )

        if update_refs:
            self._changes[-1] = _NamespaceChange(
                namespace=namespace,
                name=name,
                old=old,
                new=new,
                update_refs=update_refs,
                module_refs_only=module_refs_only,
                module_ref_changes=self._apply_ref_updates(old, new, module_refs_only=module_refs_only),
            )

        return new

    def patch_type(self, module, name):
        namespace = self._namespace(module)
        value = namespace.get(name, _MISSING)
        if value is _MISSING:
            raise KeyError(name)
        if not isinstance(value, type):
            raise TypeError(f"cannot patch_type {type(value).__name__!r} object")

        return self.proxy(module, name)

    def proxy(self, module, name, *, update_refs=None, module_refs_only=None):
        namespace = self._namespace(module)
        value = namespace.get(name, _MISSING)
        if value is _MISSING:
            raise KeyError(name)
        update_refs, module_refs_only = self._resolve_update_refs(update_refs, module_refs_only)

        new = self.patch_value(value)

        self._track_object(namespace, name, value, new)

        if new is not value:
            namespace[name] = new
            self.record_type_replacement(value, new)
            self._changes.append(
                _NamespaceChange(
                    namespace=namespace,
                    name=name,
                    old=value,
                    new=new,
                    update_refs=update_refs,
                    module_refs_only=module_refs_only,
                    module_ref_changes=(),
                )
            )

            if update_refs:
                self._changes[-1] = _NamespaceChange(
                    namespace=namespace,
                    name=name,
                    old=value,
                    new=new,
                    update_refs=update_refs,
                    module_refs_only=module_refs_only,
                    module_ref_changes=self._apply_ref_updates(value, new, module_refs_only=module_refs_only),
                )

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
                restore_module_refs(change.module_ref_changes)
                if not change.module_refs_only:
                    update(change.new, change.old)

        self._changes.clear()
        self._module_objects.clear()
        self._type_replacements.clear()
        self._uninstalled = True
