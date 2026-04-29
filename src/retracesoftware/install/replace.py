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

    def __init__(self, modules=None):
        self._refs = {}
        self._namespaces = set()
        self._track_sys_modules = modules is None
        self.refresh(tuple(sys.modules.values()) if modules is None else modules)

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

        return changes


def restore_module_refs(changes):
    for namespace, key, old, new in reversed(changes):
        if namespace.get(key) is new:
            namespace[key] = old
