"""Replay-context policy helpers for the gate-based proxy system."""

import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.proxy.stubfactory import StubRef


class _ReplayStubFactory:
    """Create lightweight stub instances from StubRef metadata."""

    def __init__(self):
        self._cache = {}

    def __call__(self, spec):
        if spec not in self._cache:
            self._cache[spec] = self._create_stubtype(spec)
        stubtype = self._cache[spec]
        return stubtype.__new__(stubtype)

    @staticmethod
    def _create_stubtype(spec):
        slots = {"__module__": spec.module}
        for method in spec.methods:
            def _noop(self, *args, **kwargs):
                pass

            _noop.__name__ = method
            slots[method] = _noop
        return type(spec.name, (object,), slots)


def replay_context(system, reader, normalize=None):
    """Build the replay gate context for *system*."""

    checkpoint = functional.sequence(normalize, reader.checkpoint) if normalize else None

    if hasattr(reader, "type_deserializer"):
        reader.type_deserializer[StubRef] = _ReplayStubFactory()

    if hasattr(reader, "stub_factory"):
        reader.stub_factory = system.disable_for(reader.stub_factory)

    if hasattr(reader, "_mark_retraced"):
        reader._mark_retraced = system.is_bound.add

    stream = getattr(reader, "_stream", None)
    if stream is not None and hasattr(stream, "_mark_retraced"):
        stream._mark_retraced = system.is_bound.add
    if stream is not None and hasattr(stream, "stub_factory"):
        stream.stub_factory = system.disable_for(stream.stub_factory)

    native_reader = getattr(reader, "_native_reader", reader)
    if hasattr(native_reader, "stub_factory"):
        native_reader.stub_factory = system.disable_for(native_reader.stub_factory)

    def remember_bind(obj):
        system.is_bound.add(obj)
        return reader.bind(obj)

    def remember_materialized_bind(obj):
        if not system.should_proxy(obj) or system.is_bound(obj):
            return obj

        system._bind(obj)
        system.is_bound.add(obj)
        return obj

    return system._create_context(
        _bind=remember_bind,
        replay_bind_materialized=remember_materialized_bind,
        int_spec=system._create_int_spec(
            bind=remember_bind,
            on_result=checkpoint,
            on_error=checkpoint,
        ),
        ext_spec=system._create_ext_spec(
            sync=reader.sync,
            track=None,
            on_result=checkpoint,
            on_error=checkpoint,
            disabled_handler=functional.mapargs(
                starting=1,
                transform=utils.try_unwrap,
                function=functional.apply,
            ),
            internal_handler=functional.mapargs(
                starting=1,
                transform=utils.try_unwrap,
                function=functional.apply,
            ),
        ),
        ext_runner=reader.read_result,
    )
