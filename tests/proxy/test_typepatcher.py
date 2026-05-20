import sys
import types

import retracesoftware.utils as utils


class _FakeSpace:
    current = None
    _next_id = 1

    def __init__(self):
        self.id = _FakeSpace._next_id
        _FakeSpace._next_id += 1

    @property
    def apply(self):
        def apply(function, *args, **kwargs):
            previous = _FakeSpace.current
            _FakeSpace.current = self
            try:
                return function(*args, **kwargs)
            finally:
                _FakeSpace.current = previous

        return apply

    def wrap(self, function):
        def wrapped(*args, **kwargs):
            return self.apply(function, *args, **kwargs)

        return wrapped


class _FakeSpaceDispatch:
    def __init__(self, default, cases=()):
        self.default = default
        self.mapping = {}
        for space, function in cases:
            self[space] = function

    def _key(self, space):
        return space.id if hasattr(space, "id") else space

    def __setitem__(self, space, function):
        self.mapping[self._key(space)] = function

    def __call__(self, *args, **kwargs):
        space = _FakeSpace.current
        key = space.id if space is not None else None
        return self.mapping.get(key, self.default)(*args, **kwargs)


def _fake_retrace():
    _FakeSpace.current = None
    _FakeSpace._next_id = 1
    return types.SimpleNamespace(
        CoordinateSpace=_FakeSpace,
        root_space=_FakeSpace(),
        space_dispatch=lambda default, cases=(): _FakeSpaceDispatch(default, cases),
    )


sys.modules["retrace"] = _fake_retrace()

from retracesoftware.gateway import GatewayPair
import retracesoftware.gateway._dynamicproxy as dynamicproxy
import retracesoftware.gateway._gatewaypair as gatewaypair_module
from retracesoftware.proxy.typepatcher import TypePatcher


def _is_passthrough(value):
    return isinstance(value, (str, int, type(None)))


def _create_pair(
    monkeypatch,
    *,
    is_passthrough=_is_passthrough,
    callbacks=None,
    results=None,
    errors=None,
    bound=None,
):
    fake_retrace = _fake_retrace()
    monkeypatch.setattr(gatewaypair_module, "retrace", fake_retrace)
    monkeypatch.setattr(dynamicproxy, "retrace", fake_retrace)
    callbacks = [] if callbacks is None else callbacks
    results = [] if results is None else results
    errors = [] if errors is None else errors
    bound = [] if bound is None else bound
    return GatewayPair.create_recording_pair(
        is_passthrough=is_passthrough,
        on_callback=lambda *args, **kwargs: callbacks.append((args, kwargs)),
        on_error=lambda *args: errors.append(args),
        on_result=results.append,
        bind=bound.append,
    )


def test_type_patcher_routes_base_methods_through_external_gateway(monkeypatch):
    results = []
    bound = []
    patcher = None

    def is_passthrough(value):
        return (
            _is_passthrough(value)
            or (
                patcher is not None
                and (
                    type(value) in patcher.patched_types
                    or value in patcher.patched_types
                )
            )
        )

    pair = _create_pair(
        monkeypatch,
        is_passthrough=is_passthrough,
        results=results,
        bound=bound,
    )
    patcher = TypePatcher(pair, bind=bound.append)

    class External:
        def ping(self, value):
            return f"pong:{value}"

    try:
        patcher.patch_type(External)

        assert External().ping("x") == "pong:x"
        assert results == ["pong:x"]
        assert External in patcher.patched_types
        assert any(isinstance(value, utils._WrappedBase) for value in bound)
    finally:
        patcher.unpatch_all()

    assert External not in patcher.patched_types
    assert External().ping("x") == "pong:x"


def test_type_patcher_routes_subclass_overrides_through_internal_gateway(monkeypatch):
    callbacks = []
    patcher = None

    def is_passthrough(value):
        return (
            _is_passthrough(value)
            or (
                patcher is not None
                and (
                    type(value) in patcher.patched_types
                    or value in patcher.patched_types
                )
            )
        )

    pair = _create_pair(monkeypatch, is_passthrough=is_passthrough, callbacks=callbacks)
    patcher = TypePatcher(pair)

    class External:
        def callback(self, value):
            return value

    class Child(External):
        def callback(self, value):
            return f"child:{value}"

    try:
        patcher.patch_type(External)

        assert Child().callback("x") == "child:x"
        assert callbacks
        assert callbacks[-1][0][0].__name__ == "callback"
        assert callbacks[-1][0][2:] == ("x",)
    finally:
        patcher.unpatch_all()


def test_type_patcher_patches_future_subclasses(monkeypatch):
    callbacks = []
    patcher = None

    def is_passthrough(value):
        return (
            _is_passthrough(value)
            or (
                patcher is not None
                and (
                    type(value) in patcher.patched_types
                    or value in patcher.patched_types
                )
            )
        )

    pair = _create_pair(monkeypatch, is_passthrough=is_passthrough, callbacks=callbacks)
    patcher = TypePatcher(pair)

    class External:
        def callback(self, value):
            return value

    try:
        patcher.patch_type(External)

        class LaterChild(External):
            def callback(self, value):
                return f"later:{value}"

        assert LaterChild().callback("x") == "later:x"
        assert LaterChild in patcher.patched_types
        assert callbacks
    finally:
        patcher.unpatch_all()
