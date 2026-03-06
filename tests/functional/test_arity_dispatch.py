import pytest
import retracesoftware.functional as fn


class TestArityDispatchCreation:

    def test_creates_with_two_handlers(self):
        d = fn.arity_dispatch(lambda: 'zero', lambda *a: 'generic')
        assert callable(d)

    def test_creates_with_many_handlers(self):
        d = fn.arity_dispatch(
            lambda: 0, lambda x: 1, lambda x, y: 2, lambda *a: 99)
        assert callable(d)

    def test_rejects_fewer_than_two_handlers(self):
        with pytest.raises(TypeError):
            fn.arity_dispatch(lambda: None)

    def test_rejects_zero_handlers(self):
        with pytest.raises(TypeError):
            fn.arity_dispatch()

    def test_rejects_non_callable_handler(self):
        with pytest.raises(TypeError):
            fn.arity_dispatch(42, lambda *a: None)

    def test_rejects_non_callable_in_middle(self):
        with pytest.raises(TypeError):
            fn.arity_dispatch(lambda: None, "not callable", lambda *a: None)


class TestArityDispatchRouting:

    def test_zero_args(self):
        d = fn.arity_dispatch(lambda: 'zero', lambda *a: 'generic')
        assert d() == 'zero'

    def test_one_arg(self):
        d = fn.arity_dispatch(
            lambda: 'zero', lambda x: f'one({x})', lambda *a: 'generic')
        assert d(42) == 'one(42)'

    def test_two_args(self):
        d = fn.arity_dispatch(
            lambda: 0, lambda x: 1, lambda x, y: x + y, lambda *a: 99)
        assert d(3, 4) == 7

    def test_fallback_on_excess_args(self):
        d = fn.arity_dispatch(lambda: 'zero', lambda *a: f'generic({len(a)})')
        assert d(1) == 'generic(1)'
        assert d(1, 2) == 'generic(2)'
        assert d(1, 2, 3) == 'generic(3)'

    def test_fallback_receives_all_args(self):
        received = []
        def generic(*args):
            received.append(args)
            return sum(args)

        d = fn.arity_dispatch(lambda: 0, generic)
        assert d(10, 20, 30) == 60
        assert received == [(10, 20, 30)]

    def test_kwargs_forwarded(self):
        d = fn.arity_dispatch(
            lambda **kw: kw,
            lambda x, **kw: (x, kw),
            lambda *a, **kw: (a, kw))

        assert d() == {}
        assert d(1, key='val') == (1, {'key': 'val'})
        assert d(1, 2, key='val') == ((1, 2), {'key': 'val'})

    def test_exact_boundary(self):
        """Handler count exactly matches nargs — last handler is the fallback."""
        d = fn.arity_dispatch(
            lambda: 0, lambda x: 1, lambda *args: len(args))
        assert d() == 0
        assert d('a') == 1
        assert d('a', 'b') == 2
        # 3+ args also go to handler[2] (the last/generic)
        assert d('a', 'b', 'c') == 3


class TestArityDispatchEdgeCases:

    def test_handler_exceptions_propagate(self):
        def boom():
            raise ValueError("kaboom")

        d = fn.arity_dispatch(boom, lambda *a: 'ok')
        with pytest.raises(ValueError, match="kaboom"):
            d()

    def test_repr_contains_type_name(self):
        d = fn.arity_dispatch(lambda: None, lambda *a: None)
        assert 'arity_dispatch' in repr(d)

    def test_method_descriptor_binding(self):
        """arity_dispatch supports METHOD_DESCRIPTOR for use as methods.
        Note: handler indices include 'self' — foo.handle() has nargs=1."""
        class Foo:
            # handler[0] = no args at all (class-level call, no self)
            # handler[1] = self only (foo.handle())
            # handler[2] = self + 1+ args (foo.handle(x, ...))
            handle = fn.arity_dispatch(
                lambda: 'class_level',
                lambda self: f'zero({self})',
                lambda self, *a: f'args({self}, {a})')

        foo = Foo()
        assert foo.handle() == f'zero({foo})'
        assert foo.handle(1) == f'args({foo}, (1,))'
        assert foo.handle(1, 2) == f'args({foo}, (1, 2))'


class TestArityDispatchPerformance:
    """Verify the dispatch is a thin layer — same result as direct calls."""

    def test_passthrough_matches_direct(self):
        def add(a, b): return a + b
        def zero(): return 0
        def one(x): return x * 2

        d = fn.arity_dispatch(zero, one, add)

        assert d() == zero()
        assert d(5) == one(5)
        assert d(3, 4) == add(3, 4)
