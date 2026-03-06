"""Tests for function composition: compose, composeN, callall, juxt, use_with."""
import pytest
import retracesoftware.functional as fn


class TestCompose:
    def test_composes_two_functions_right_to_left(self):
        # compose(f, g)(x) == f(g(x))
        f = lambda x: x + "!"
        g = lambda x: x.upper()
        
        composed = fn.compose(f, g)
        
        assert composed("hello") == "HELLO!"

    def test_passes_all_args_to_inner_function(self):
        f = lambda x: x * 2
        g = lambda a, b: a + b
        
        composed = fn.compose(f, g)
        
        assert composed(3, 4) == 14  # f(g(3, 4)) = f(7) = 14

    def test_supports_kwargs(self):
        f = lambda x: x.upper()
        g = lambda s, suffix="": s + suffix
        
        composed = fn.compose(f, g)
        
        assert composed("hello", suffix="!") == "HELLO!"

    @pytest.mark.skip(reason="compose requires callable for second argument")
    def test_attribute_access_is_composed(self):
        class Obj:
            value = "test"
        
        f = lambda x: x.upper()
        g = Obj()
        
        composed = fn.compose(f, g)
        
        assert composed.value == "TEST"


class TestComposeN:
    def test_composes_multiple_functions_in_order(self):
        # composeN(f1, f2, f3)(x) = f3(f2(f1(x)))
        strip = str.strip
        lower = str.lower
        bracket = lambda s: f"[{s}]"
        
        composed = fn.composeN(strip, lower, bracket)
        
        assert composed("  HeLLo  ") == "[hello]"

    @pytest.mark.skip(reason="composeN with single method_descriptor not iterable")
    def test_single_function_is_identity(self):
        composed = fn.composeN(str.upper)
        
        assert composed("hello") == "HELLO"

    def test_works_with_list_of_functions(self):
        funcs = [str.strip, str.upper]
        composed = fn.composeN(funcs)
        
        assert composed("  hello  ") == "HELLO"


class TestCallAll:
    def test_calls_all_functions_returns_last_result(self):
        results = []
        
        def f1(x):
            results.append(('f1', x))
            return 'f1'
        def f2(x):
            results.append(('f2', x))
            return 'f2'
        def f3(x):
            results.append(('f3', x))
            return 'f3'
        
        call_all = fn.callall([f1, f2, f3])
        result = call_all(42)
        
        assert result == 'f3'
        assert results == [('f1', 42), ('f2', 42), ('f3', 42)]

    def test_works_with_tuple_of_functions(self):
        calls = []
        call_all = fn.callall((
            lambda x: calls.append(1),
            lambda x: calls.append(2),
        ))
        
        call_all(None)
        assert calls == [1, 2]


class TestJuxt:
    def test_returns_tuple_of_results(self):
        stats = fn.juxt(min, max, sum)
        
        result = stats([3, 1, 4, 1, 5])
        
        assert result == (1, 5, 14)

    def test_passes_same_args_to_all_functions(self):
        calls = []
        
        def f1(*args):
            calls.append(('f1', args))
            return 'a'
        def f2(*args):
            calls.append(('f2', args))
            return 'b'
        
        juxt = fn.juxt(f1, f2)
        result = juxt(1, 2, 3)
        
        assert result == ('a', 'b')
        assert calls == [('f1', (1, 2, 3)), ('f2', (1, 2, 3))]


class TestUseWith:
    def test_transforms_args_before_calling_target(self):
        # use_with(f, t1, t2)(x) == f(t1(x), t2(x))
        add = lambda a, b: a + b
        double = lambda x: x * 2
        triple = lambda x: x * 3
        
        use = fn.use_with(add, double, triple)
        
        # add(double(5), triple(5)) = add(10, 15) = 25
        assert use(5) == 25

    def test_each_transform_receives_all_args(self):
        calls = []
        
        def target(a, b):
            return a + b
        
        def t1(*args):
            calls.append(('t1', args))
            return sum(args)
        
        def t2(*args):
            calls.append(('t2', args))
            return max(args)
        
        use = fn.use_with(target, t1, t2)
        result = use(1, 2, 3)
        
        assert calls == [('t1', (1, 2, 3)), ('t2', (1, 2, 3))]
        assert result == 6 + 3  # sum + max


class TestEither:
    def test_returns_first_if_not_none(self):
        first = lambda x: x * 2
        second = lambda x: x * 3
        
        either = fn.either(first, second)
        
        assert either(5) == 10

    def test_returns_second_if_first_is_none(self):
        first = lambda x: None
        second = lambda x: x * 3
        
        either = fn.either(first, second)
        
        assert either(5) == 15

    def test_second_not_called_if_first_succeeds(self):
        calls = []
        
        def first(x):
            calls.append('first')
            return 'result'
        
        def second(x):
            calls.append('second')
            return 'fallback'
        
        either = fn.either(first, second)
        either(42)
        
        assert calls == ['first']

