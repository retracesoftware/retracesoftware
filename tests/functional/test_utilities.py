"""Tests for utility functions: always, constantly, cond, first, firstof, lazy, anyargs, selfapply."""
import pytest
import retracesoftware.functional as fn


class TestAlways:
    def test_returns_value_when_not_callable(self):
        always_42 = fn.always(42)
        
        assert always_42() == 42
        assert always_42("ignored", "args") == 42

    def test_calls_callable_with_no_args(self):
        counter = [0]
        def increment():
            counter[0] += 1
            return counter[0]
        
        always_inc = fn.always(increment)
        
        assert always_inc("ignored") == 1
        assert always_inc() == 2
        assert always_inc(1, 2, 3) == 3

    def test_ignores_all_arguments(self):
        always_none = fn.always(None)
        
        assert always_none(1, 2, 3, key="value") is None


class TestConstantly:
    def test_always_returns_same_value(self):
        const = fn.constantly(42)
        
        assert const() == 42
        assert const(1, 2, 3) == 42
        assert const(x=1, y=2) == 42

    def test_does_not_call_callable_values(self):
        # Unlike always(), constantly() returns the value as-is
        func = lambda: "called"
        const = fn.constantly(func)
        
        result = const()
        assert result is func  # returns the function itself, not "called"

    def test_returns_none(self):
        const = fn.constantly(None)
        
        assert const() is None
        assert const(42) is None


class TestCond:
    def test_single_default_constant(self):
        c = fn.cond(42)
        assert c() == 42
        assert c(1, 2, 3) == 42

    def test_single_default_callable(self):
        c = fn.cond(lambda x: x + 1)
        assert c(10) == 11

    def test_one_clause(self):
        c = fn.cond(lambda x: x > 0, lambda x: "positive", "default")
        assert c(5) == "positive"
        assert c(-1) == "default"

    def test_multiple_clauses(self):
        c = fn.cond(
            lambda x: x < 0, lambda x: "neg",
            lambda x: x == 0, lambda x: "zero",
            "default"
        )
        assert c(-1) == "neg"
        assert c(0) == "zero"
        assert c(1) == "default"

    def test_chains_if_then_else(self):
        c = fn.cond(
            lambda x: x == 1, lambda x: "one",
            lambda x: x == 2, lambda x: "two",
            lambda x: "other"
        )
        assert c(1) == "one"
        assert c(2) == "two"
        assert c(3) == "other"

    def test_requires_odd_args(self):
        with pytest.raises(ValueError, match="odd number"):
            fn.cond(lambda x: True, lambda x: 1)

    def test_requires_at_least_one_arg(self):
        with pytest.raises(ValueError, match="at least one"):
            fn.cond()


class TestFirst:
    def test_returns_first_non_none_result(self):
        f1 = lambda x: None
        f2 = lambda x: None
        f3 = lambda x: x * 2
        f4 = lambda x: x * 3
        
        first = fn.first(f1, f2, f3, f4)
        
        assert first(5) == 10

    def test_returns_none_if_all_return_none(self):
        f1 = lambda x: None
        f2 = lambda x: None
        
        first = fn.first(f1, f2)
        
        assert first(5) is None

    def test_short_circuits_on_first_non_none(self):
        calls = []
        
        def f1(x):
            calls.append('f1')
            return None
        def f2(x):
            calls.append('f2')
            return 'result'
        def f3(x):
            calls.append('f3')
            return 'never reached'
        
        first = fn.first(f1, f2, f3)
        first(42)
        
        assert calls == ['f1', 'f2']


class TestFirstOf:
    def test_returns_first_non_none_result(self):
        f1 = lambda x: None
        f2 = lambda x: "found"
        f3 = lambda x: "not reached"
        
        firstof = fn.firstof(f1, f2, f3)
        
        assert firstof(42) == "found"

    def test_last_function_always_called_as_fallback(self):
        # firstof treats the last function specially - always calls it
        calls = []
        
        def f1(x):
            calls.append('f1')
            return None
        def fallback(x):
            calls.append('fallback')
            return "default"
        
        firstof = fn.firstof(f1, fallback)
        result = firstof(42)
        
        assert result == "default"
        assert calls == ['f1', 'fallback']


@pytest.mark.skip(reason="lazy not implemented in module")
class TestLazy:
    def test_defers_execution_until_called(self):
        calls = []
        
        def compute(x, y):
            calls.append((x, y))
            return x + y
        
        lazy = fn.lazy(compute, 1, 2)
        
        assert calls == []  # not called yet
        
        result = lazy()
        
        assert result == 3
        assert calls == [(1, 2)]

    def test_ignores_arguments_when_called(self):
        lazy = fn.lazy(lambda a, b: a * b, 3, 4)
        
        # Arguments to lazy() are ignored
        assert lazy("ignored", "args") == 12


class TestAnyArgs:
    def test_calls_function_with_no_args(self):
        counter = [0]
        def no_args():
            counter[0] += 1
            return counter[0]
        
        wrapped = fn.anyargs(no_args)
        
        assert wrapped(1, 2, 3, key="value") == 1
        assert wrapped() == 2

    def test_ignores_all_passed_arguments(self):
        def get_value():
            return "constant"
        
        wrapped = fn.anyargs(get_value)
        
        assert wrapped(None) == "constant"
        assert wrapped(1, 2, x=3) == "constant"


class TestSelfApply:
    def test_applies_result_to_same_args(self):
        # selfapply(f)(x) == f(x)(x)
        def make_multiplier(n):
            return lambda x: x * n
        
        selfapply = fn.selfapply(make_multiplier)
        
        # make_multiplier(5) returns lambda x: x*5, then called with 5 → 25
        assert selfapply(5) == 25

    def test_passes_all_args_to_both_calls(self):
        calls = []
        
        def factory(*args):
            calls.append(('factory', args))
            def inner(*args):
                calls.append(('inner', args))
                return sum(args)
            return inner
        
        selfapply = fn.selfapply(factory)
        result = selfapply(1, 2, 3)
        
        assert calls == [('factory', (1, 2, 3)), ('inner', (1, 2, 3))]
        assert result == 6


class TestRepeatedly:
    def test_calls_function_with_no_args_every_time(self):
        counter = [0]
        def increment():
            counter[0] += 1
            return counter[0]
        
        rep = fn.repeatedly(increment)
        
        assert rep() == 1
        assert rep("ignored") == 2
        assert rep(1, 2, 3) == 3

    def test_ignores_all_arguments(self):
        def constant():
            return 42
        
        rep = fn.repeatedly(constant)
        
        assert rep() == 42
        assert rep("any", "args", key="ignored") == 42

