import gc
import sys

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
    for ref in gc.get_referrers(old):
        container_replace(container = ref, old = old, new = new)


def update_module_refs(old, new):
    """Replace *old* with *new* only in module namespace dicts (sys.modules).

    Use this when *update* would be too broad and could corrupt internal
    structures (e.g. system.patched_types).  Only touches module.__dict__
    entries, which is sufficient for re-exports like io.open_code = _io.open_code.
    """
    for mod in sys.modules.values():
        if mod is None:
            continue
        d = getattr(mod, '__dict__', None)
        if d is not None and isinstance(d, dict):
            for key, value in list(d.items()):
                if value is old:
                    d[key] = new
