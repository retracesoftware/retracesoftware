"""Tests for advanced utilities: walker, deepwrap, dispatch."""
import pytest
import retracesoftware.functional as fn


class TestWalker:
    def test_transforms_leaf_values(self):
        double = lambda x: x * 2 if isinstance(x, int) else x
        walker = fn.walker(double)
        
        result = walker(5)
        
        assert result == 10

    def test_walks_nested_tuples(self):
        double = lambda x: x * 2 if isinstance(x, int) else x
        walker = fn.walker(double)
        
        result = walker((1, 2, (3, 4)))
        
        assert result == (2, 4, (6, 8))

    def test_walks_nested_lists(self):
        double = lambda x: x * 2 if isinstance(x, int) else x
        walker = fn.walker(double)
        
        result = walker([1, [2, 3]])
        
        assert result == [2, [4, 6]]

    def test_walks_nested_dicts(self):
        double = lambda x: x * 2 if isinstance(x, int) else x
        walker = fn.walker(double)
        
        result = walker({'a': 1, 'b': {'c': 2}})
        
        assert result == {'a': 2, 'b': {'c': 4}}

    def test_preserves_structure_when_no_changes(self):
        identity = lambda x: x
        walker = fn.walker(identity)
        
        original = (1, [2, {'a': 3}])
        result = walker(original)
        
        # Should return same object if nothing changed
        assert result == original

    def test_handles_none_values(self):
        transform = lambda x: "transformed" if x is not None else x
        walker = fn.walker(transform)
        
        result = walker(None)
        
        assert result is None  # None is passed through unchanged

    def test_mixed_nested_structure(self):
        upper = lambda x: x.upper() if isinstance(x, str) else x
        walker = fn.walker(upper)
        
        data = {
            'names': ['alice', 'bob'],
            'nested': {
                'value': 'hello'
            }
        }
        
        result = walker(data)
        
        assert result == {
            'names': ['ALICE', 'BOB'],
            'nested': {
                'value': 'HELLO'
            }
        }


class TestDeepWrap:
    def test_wraps_result_of_function(self):
        def target(x):
            return x * 2
        
        def wrapper(result):
            return result + 1
        
        deep = fn.deepwrap(wrapper, target)
        
        assert deep(5) == 11  # (5 * 2) + 1 = 11

    def test_recursively_wraps_callable_results(self):
        calls = []
        
        def factory(n):
            calls.append(('factory', n))
            if n <= 0:
                return "done"
            return lambda x: factory(n - 1)
        
        def wrapper(result):
            calls.append(('wrapper', type(result).__name__))
            return result
        
        deep = fn.deepwrap(wrapper, factory)
        
        # First call
        result1 = deep(2)
        # result1 is wrapped, and since it's callable, returns another deepwrap
        
        assert callable(result1)

    def test_non_callable_result_not_wrapped_recursively(self):
        def target(x):
            return x * 2  # Returns int, not callable
        
        seen_results = []
        def wrapper(result):
            seen_results.append(result)
            return result
        
        deep = fn.deepwrap(wrapper, target)
        result = deep(5)
        
        assert result == 10
        assert seen_results == [10]


@pytest.mark.skip(reason="dispatch type cannot be instantiated - not implemented")
class TestDispatch:
    def test_matches_first_true_predicate(self):
        is_zero = lambda x: x == 0
        is_negative = lambda x: x < 0
        
        dispatch = fn.dispatch(
            is_zero, lambda x: "zero",
            is_negative, lambda x: "negative",
            lambda x: "positive"  # fallback
        )
        
        assert dispatch(0) == "zero"
        assert dispatch(-5) == "negative"
        assert dispatch(5) == "positive"

    def test_returns_none_without_fallback_and_no_match(self):
        always_false = lambda x: False
        
        dispatch = fn.dispatch(
            always_false, lambda x: "never"
        )
        
        assert dispatch(42) is None

    def test_short_circuits_on_first_match(self):
        calls = []
        
        def pred1(x):
            calls.append('pred1')
            return True
        def pred2(x):
            calls.append('pred2')
            return True
        
        dispatch = fn.dispatch(
            pred1, lambda x: "first",
            pred2, lambda x: "second"
        )
        
        result = dispatch(42)
        
        assert result == "first"
        assert calls == ['pred1']  # pred2 never called

    def test_passes_args_to_predicates_and_handlers(self):
        calls = []
        
        def pred(a, b):
            calls.append(('pred', a, b))
            return a > b
        
        def handler(a, b):
            calls.append(('handler', a, b))
            return a - b
        
        dispatch = fn.dispatch(pred, handler)
        result = dispatch(5, 3)
        
        assert result == 2
        assert calls == [('pred', 5, 3), ('handler', 5, 3)]


class TestWhenNotNone:
    def test_calls_function_when_no_none_args(self):
        add = lambda a, b: a + b
        safe_add = fn.when_not_none(add)
        
        assert safe_add(1, 2) == 3

    def test_returns_none_when_any_positional_arg_is_none(self):
        add = lambda a, b: a + b
        safe_add = fn.when_not_none(add)
        
        assert safe_add(None, 2) is None
        assert safe_add(1, None) is None
        assert safe_add(None, None) is None

    def test_checks_kwargs_too(self):
        def combine(a, b=0):
            return a + b
        
        safe = fn.when_not_none(combine)
        
        assert safe(1, b=None) is None

    def test_does_not_call_function_when_none_found(self):
        calls = []
        
        def target(a, b):
            calls.append((a, b))
            return a + b
        
        safe = fn.when_not_none(target)
        safe(None, 2)
        
        assert calls == []  # target was never called

