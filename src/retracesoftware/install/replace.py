import gc
import sys

_MISSING = object()


def container_replace(container, old, new):
    if isinstance(container, dict):
        if old in container:
            elem = container.pop(old)
            container[new] = elem
            container_replace(container, old, new)
        else:
            for key,value in container.items():
                if key != '__retrace_unproxied__' and value is old:
                    container[key] = new
        return True
    elif isinstance(container, list):
        for i,value in enumerate(container):
            if value is old:
                container[i] = new
        return True
    elif isinstance(container, set):
        container.remove(old)
        container.add(new)
        return True
    else:
        return False

def update(old, new):
    # Temporary safety valve: the global gc.get_referrers() rewrite path is
    # too expensive and is currently causing uninstall hangs. Keep module-level
    # namespace rewrites enabled via update_module_refs(), but disable the
    # broad object-graph sweep for now.
    return None


def update_module_refs(old, new):
    changes = []
    for module in tuple(sys.modules.values()):
        namespace = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict):
            continue

        for key, value in tuple(namespace.items()):
            if key == "__retrace_unproxied__":
                continue
            if value is old:
                namespace[key] = new
                changes.append((namespace, key, old, new))

    return changes


class ModuleRefIndex:
    """Indexed module-namespace replacements for install-time patching."""

    def __init__(self, modules=None, *, global_scope=False):
        self._refs = {}
        self._class_refs = {}
        self._namespaces = set()
        self._class_owners = set()
        self._track_sys_modules = modules is None
        self._global_scope = global_scope
        self.refresh(tuple(sys.modules.values()) if modules is None else modules)
        if global_scope:
            self.refresh_global_class_attrs()

    def refresh(self, modules=None):
        if modules is None:
            modules = tuple(sys.modules.values())

        for module in modules:
            namespace = getattr(module, "__dict__", None)
            if not isinstance(namespace, dict):
                continue

            namespace_id = id(namespace)
            if namespace_id in self._namespaces:
                continue
            self._namespaces.add(namespace_id)

            for key, value in tuple(namespace.items()):
                if key == "__retrace_unproxied__":
                    continue
                self._refs.setdefault(id(value), []).append([namespace, key, value])
                if self._global_scope and isinstance(value, type):
                    self._refresh_class_attrs(value)

    def refresh_global_class_attrs(self):
        """Index class attributes once for startup-time cached factories."""
        for obj in tuple(gc.get_objects()):
            if isinstance(obj, type):
                self._refresh_class_attrs(obj)

    def _refresh_class_attrs(self, owner):
        owner_id = id(owner)
        if owner_id in self._class_owners:
            return

        try:
            items = tuple(owner.__dict__.items())
        except Exception:
            return

        self._class_owners.add(owner_id)

        for key, value in items:
            if key == "__retrace_unproxied__":
                continue
            self._class_refs.setdefault(id(value), []).append([owner, key, value])

    def replace(self, old, new):
        if self._track_sys_modules:
            self.refresh()

        changes = []
        entries = self._refs.pop(id(old), ())

        for entry in entries:
            namespace, key, expected = entry
            current = namespace.get(key, _MISSING)
            if current is _MISSING:
                continue

            if current is old:
                namespace[key] = new
                changes.append((namespace, key, old, new))
                entry[2] = new
                self._refs.setdefault(id(new), []).append(entry)
            else:
                entry[2] = current
                self._refs.setdefault(id(current), []).append(entry)

        if self._global_scope:
            changes.extend(self._replace_class_attrs(old, new))

        return changes

    def _replace_class_attrs(self, old, new):
        changes = []
        entries = self._class_refs.pop(id(old), ())

        for entry in entries:
            owner, key, expected = entry
            try:
                current = owner.__dict__.get(key, _MISSING)
            except Exception:
                continue

            if current is _MISSING:
                continue

            if current is old:
                try:
                    setattr(owner, key, new)
                except Exception:
                    entry[2] = current
                    self._class_refs.setdefault(id(current), []).append(entry)
                    continue

                changes.append(("class_attr", owner, key, old, new))
                entry[2] = new
                self._class_refs.setdefault(id(new), []).append(entry)
            else:
                entry[2] = current
                self._class_refs.setdefault(id(current), []).append(entry)

        return changes


def restore_module_refs(changes):
    for change in reversed(changes):
        if len(change) == 5 and change[0] == "class_attr":
            _, owner, key, old, new = change
            try:
                if owner.__dict__.get(key, _MISSING) is new:
                    setattr(owner, key, old)
            except Exception:
                pass
            continue

        namespace, key, old, new = change
        if namespace.get(key) is new:
            namespace[key] = old
