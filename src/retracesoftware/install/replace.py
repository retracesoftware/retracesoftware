import gc

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
