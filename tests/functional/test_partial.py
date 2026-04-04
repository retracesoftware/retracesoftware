import pytest

import retracesoftware.functional as fn


def test_partial_applies_positional_and_keyword_arguments():
    calls = []

    def combine(a, b, c=0):
        calls.append((a, b, c))
        return a + b + c

    partial_add = fn.partial(combine, 1)

    assert partial_add(2, c=3) == 6
    assert partial_add(5) == 6
    assert calls == [(1, 2, 3), (1, 5, 0)]


def test_partial_required_zero_delays_until_invocation():
    calls = []

    def compute(x):
        calls.append(x)
        return x * 2

    lazy = fn.partial(compute, 4, required=0)

    assert calls == []
    assert lazy() == 8
    assert calls == [4]


def test_partial_bound_args_support_index_get_and_set():
    calls = []

    def combine(a, b, c):
        calls.append((a, b, c))
        return a + b + c

    partial_add = fn.partial(combine, 1, 2)

    assert len(partial_add) == 2
    assert partial_add[0] == 1
    assert partial_add[1] == 2
    assert partial_add[-1] == 2

    partial_add[0] = 10
    partial_add[-1] = 20

    assert partial_add[0] == 10
    assert partial_add[1] == 20
    assert partial_add(3) == 33
    assert calls == [(10, 20, 3)]

    with pytest.raises(IndexError):
        _ = partial_add[2]
