import retracesoftware.functional as fn


def test_memoize_one_arg_caches_by_identity():
    calls = []

    def target(x):
        calls.append(id(x))
        return f"val-{id(x)}"

    memo = fn.memoize_one_arg(target)
    obj = object()

    assert memo(obj) == f"val-{id(obj)}"
    assert memo(obj) == f"val-{id(obj)}"
    assert calls == [id(obj)]


