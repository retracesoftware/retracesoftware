import retracesoftware.functional as fn


def test_compose_two_functions():
    def f(x):
        return f"{x}!"

    def g(x):
        return x.upper()

    combo = fn.compose(f, g)
    assert combo("hi") == "HI!"


def test_composeN_chains_multiple_callables():
    combo = fn.composeN(str.strip, str.lower, lambda s: f"[{s}]")
    assert combo("  HeLLo  ") == "[hello]"

