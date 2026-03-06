import retracesoftware.functional as fn


def test_when_not_none_short_circuits_on_none_arguments():
    calls = []

    def combine(a, b):
        calls.append((a, b))
        return a + b

    wrapper = fn.when_not_none(combine)

    assert wrapper(1, 2) == 3
    assert wrapper(None, 2) is None
    assert calls == [(1, 2)]


def test_if_then_else_routes_to_correct_branch():
    calls = []

    def is_positive(x):
        calls.append(("pred", x))
        return x > 0

    def then_fn(x):
        calls.append(("then", x))
        return x + 1

    def else_fn(x):
        calls.append(("else", x))
        return -x

    gate = fn.if_then_else(is_positive, then_fn, else_fn)

    assert gate(1) == 2
    assert gate(-2) == 2
    assert calls == [("pred", 1), ("then", 1), ("pred", -2), ("else", -2)]

