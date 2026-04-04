import retracesoftware.functional as fn


def test_when_returns_input_when_predicate_does_not_match():
    calls = []

    def is_positive(x):
        calls.append(("pred", x))
        return x > 0

    def wrap(x):
        calls.append(("then", x))
        return [x]

    wrapper = fn.when(is_positive, wrap)

    assert wrapper(-3) == -3
    assert wrapper(2) == [2]
    assert calls == [("pred", -3), ("pred", 2), ("then", 2)]


def test_when_not_none_short_circuits_on_none_arguments():
    calls = []

    def combine(a, b):
        calls.append((a, b))
        return a + b

    wrapper = fn.when_not_none(combine)

    assert wrapper(1, 2) == 3
    assert wrapper(None, 2) is None
    assert calls == [(1, 2)]


def test_when_not_returns_input_when_predicate_matches():
    calls = []

    def is_str(x):
        calls.append(("pred", x))
        return isinstance(x, str)

    def wrap(x):
        calls.append(("action", x))
        return [x]

    wrapper = fn.when_not(is_str, wrap)

    assert wrapper("ok") == "ok"
    assert wrapper(3) == [3]
    assert calls == [("pred", "ok"), ("pred", 3), ("action", 3)]


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


def test_if_then_else_uses_when_for_identity_else_branch():
    calls = []

    def is_positive(x):
        calls.append(("pred", x))
        return x > 0

    def wrap(x):
        calls.append(("then", x))
        return [x]

    gate = fn.if_then_else(is_positive, wrap, fn.identity)

    assert gate(-1) == -1
    assert gate(2) == [2]
    assert calls == [("pred", -1), ("pred", 2), ("then", 2)]


def test_if_then_else_uses_when_not_for_identity_then_branch():
    calls = []

    def is_str(x):
        calls.append(("pred", x))
        return isinstance(x, str)

    def wrap(x):
        calls.append(("else", x))
        return [x]

    gate = fn.if_then_else(is_str, fn.identity, wrap)

    assert gate("ok") == "ok"
    assert gate(3) == [3]
    assert calls == [("pred", "ok"), ("pred", 3), ("else", 3)]


def test_if_then_else_returns_identity_when_both_branches_are_identity():
    gate = fn.if_then_else(lambda x: x > 0, fn.identity, fn.identity)

    marker = object()
    assert gate(marker) is marker
