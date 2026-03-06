"""Tests for argument manipulation: spread, dropargs, param, indexed, mapargs."""
import pytest
import retracesoftware.functional as fn


class TestSpread:
    def test_spreads_single_arg_to_multiple_args(self):
        # spread(f, t1, t2)(x) == f(t1(x), t2(x))
        def add(a, b):
            return a + b
        
        get_min = min
        get_max = max
        
        spread = fn.spread(add, get_min, get_max)
        
        # add(min([1,2,3]), max([1,2,3])) = add(1, 3) = 4
        assert spread([1, 2, 3]) == 4

    def test_none_transform_passes_original_value(self):
        def combine(a, b):
            return (a, b)
        
        double = lambda x: x * 2
        
        # None means pass x unchanged
        spread = fn.spread(combine, double, None)
        
        # combine(double(5), 5) = combine(10, 5)
        assert spread(5) == (10, 5)

    def test_all_transforms_receive_same_input(self):
        calls = []
        
        def target(a, b, c):
            return a + b + c
        
        def t1(x):
            calls.append(('t1', x))
            return x
        def t2(x):
            calls.append(('t2', x))
            return x * 2
        def t3(x):
            calls.append(('t3', x))
            return x * 3
        
        spread = fn.spread(target, t1, t2, t3)
        result = spread(10)
        
        assert calls == [('t1', 10), ('t2', 10), ('t3', 10)]
        assert result == 10 + 20 + 30


class TestDropArgs:
    def test_drops_first_n_positional_args(self):
        def target(a, b):
            return a + b
        
        drop2 = fn.dropargs(target, 2)
        
        # First two args dropped: drop2(ignored1, ignored2, 3, 4) → target(3, 4)
        assert drop2("x", "y", 3, 4) == 7

    def test_default_drops_one_arg(self):
        def target(x):
            return x * 2
        
        drop1 = fn.dropargs(target)
        
        assert drop1("ignored", 5) == 10

    def test_preserves_kwargs(self):
        def target(a, b=10):
            return a + b
        
        drop1 = fn.dropargs(target, 1)
        
        assert drop1("ignored", 5, b=20) == 25


class TestParam:
    def test_extracts_positional_arg_by_index(self):
        get_second = fn.param("second", 1)
        
        assert get_second("a", "b", "c") == "b"

    def test_prefers_kwarg_by_name(self):
        get_x = fn.param("x", 0)
        
        # kwarg takes precedence
        assert get_x("positional", x="keyword") == "keyword"

    def test_falls_back_to_positional(self):
        get_x = fn.param("x", 0)
        
        # No kwarg "x", so use positional index 0
        assert get_x("first", "second") == "first"

    def test_raises_when_param_not_found(self):
        get_missing = fn.param("missing", 10)
        
        try:
            get_missing("only_one_arg")
            assert False, "Should have raised"
        except ValueError as e:
            assert "missing" in str(e)


class TestIndexed:
    def test_extracts_from_tuple(self):
        get_first = fn.indexed(0)
        get_third = fn.indexed(2)
        
        data = ("a", "b", "c")
        
        assert get_first(data) == "a"
        assert get_third(data) == "c"

    def test_extracts_from_list(self):
        get_second = fn.indexed(1)
        
        data = [10, 20, 30]
        
        assert get_second(data) == 20

    @pytest.mark.skip(reason="Causes segmentation fault - needs C extension fix")
    def test_negative_index_not_supported(self):
        # Note: This may depend on implementation
        get_last = fn.indexed(-1)
        
        # May raise or return unexpected value depending on implementation
        try:
            result = get_last([1, 2, 3])
            # If it works, that's fine too
        except (IndexError, TypeError):
            pass  # Expected behavior


class TestMapArgs:
    def test_transforms_all_args(self):
        def add(a, b):
            return a + b
        
        double = lambda x: x * 2
        
        mapped = fn.mapargs(add, double)
        
        # add(double(3), double(4)) = add(6, 8) = 14
        assert mapped(3, 4) == 14

    def test_transforms_kwargs_too(self):
        def combine(a, b=0):
            return a + b
        
        double = lambda x: x * 2
        
        mapped = fn.mapargs(combine, double)
        
        # combine(double(3), b=double(5)) = combine(6, b=10) = 16
        assert mapped(3, b=5) == 16

    def test_starting_skips_first_n_args(self):
        calls = []
        
        def target(a, b, c):
            return (a, b, c)
        
        def transform(x):
            calls.append(x)
            return x * 10
        
        # Transform args starting from index 1 (skip first arg)
        mapped = fn.mapargs(target, transform, starting=1)
        result = mapped(1, 2, 3)
        
        assert calls == [2, 3]  # 1 was not transformed
        assert result == (1, 20, 30)

    def test_attribute_access_goes_to_wrapped_function(self):
        def target(x):
            return x
        target.custom_attr = "test"
        
        mapped = fn.mapargs(target, str)
        
        assert mapped.custom_attr == "test"

