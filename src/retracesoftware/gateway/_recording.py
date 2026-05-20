from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from retracesoftware.gateway import _gatewaypair


@dataclass(frozen=True)
class Bind:
    handle: int
    value: Any


@dataclass(frozen=True)
class Bound:
    handle: int


@dataclass(frozen=True)
class Result:
    value: Any


@dataclass(frozen=True)
class Error:
    exc_type: type
    exc_value: BaseException
    traceback: Any


@dataclass(frozen=True)
class Callback:
    function: Callable[..., Any]
    args: tuple
    kwargs: dict


RecordingEvent = Bind | Result | Error | Callback


def _encode(value: Any, handles_by_id: dict[int, int]) -> Any:
    handle = handles_by_id.get(id(value))
    if handle is not None:
        return Bound(handle)
    if isinstance(value, tuple):
        return tuple(_encode(item, handles_by_id) for item in value)
    if isinstance(value, list):
        return [_encode(item, handles_by_id) for item in value]
    if isinstance(value, dict):
        return {
            _encode(key, handles_by_id): _encode(item, handles_by_id)
            for key, item in value.items()
        }
    return value


def _resolve(value: Any, bindings: dict[int, Any]) -> Any:
    if isinstance(value, Bound):
        return bindings[value.handle]
    if isinstance(value, tuple):
        return tuple(_resolve(item, bindings) for item in value)
    if isinstance(value, list):
        return [_resolve(item, bindings) for item in value]
    if isinstance(value, dict):
        return {
            _resolve(key, bindings): _resolve(item, bindings)
            for key, item in value.items()
        }
    return value


def _consume_binds(events: list[RecordingEvent], bindings: dict[int, Any]) -> None:
    while events:
        event = events[0]
        if not isinstance(event, Bind):
            return
        events.pop(0)
        bindings[event.handle] = event.value


def _consume_next(events: list[RecordingEvent], bindings: dict[int, Any]) -> RecordingEvent:
    _consume_binds(events, bindings)
    if events:
        return events.pop(0)
    raise AssertionError("replay event list is empty")


def create_recording_pair_recorder(
    *,
    record: Callable[[RecordingEvent], Any],
    is_passthrough: _gatewaypair.PassthroughPredicate,
    bindings: dict[int, Any] | None = None,
) -> _gatewaypair.GatewayPair:
    """Create a recording pair that emits typed events to ``record``.

    ``is_passthrough`` is a predicate, not a flag: it is called with each value
    that may need proxying, and returns ``True`` only when that value can cross
    unchanged.
    """
    bindings = {} if bindings is None else bindings
    handles_by_id: dict[int, int] = {}
    next_handle = [1]

    def bind(value: Any) -> None:
        key = id(value)
        if key in handles_by_id:
            return
        handle = next_handle[0]
        next_handle[0] += 1
        handles_by_id[key] = handle
        bindings[handle] = value
        record(Bind(handle, value))

    return _gatewaypair.GatewayPair.create_recording_pair(
        is_passthrough=is_passthrough,
        on_callback=lambda function, *args, **kwargs: record(
            Callback(
                function,
                _encode(args, handles_by_id),
                _encode(kwargs, handles_by_id),
            )
        ),
        on_error=lambda exc_type, exc_value, traceback: record(
            Error(exc_type, exc_value, traceback)
        ),
        on_result=lambda value: record(Result(_encode(value, handles_by_id))),
        bind=bind,
    )


def create_replay_pair_recorder(
    *,
    events: list[RecordingEvent],
    is_passthrough: _gatewaypair.PassthroughPredicate,
    bindings: dict[int, Any] | None = None,
) -> _gatewaypair.GatewayPair:
    """Create a replay pair that consumes typed events from ``events``."""
    bindings = {} if bindings is None else bindings

    def next_result(*_args: Any, **_kwargs: Any) -> Any:
        event = _consume_next(events, bindings)
        if isinstance(event, Result):
            return _resolve(event.value, bindings)
        if isinstance(event, Error):
            raise event.exc_value
        raise AssertionError(f"expected Result or Error event, got {event!r}")

    pair = _gatewaypair.GatewayPair.create_replay_pair(
        is_passthrough=is_passthrough,
        next_result=next_result,
        bind=lambda value: None,
    )

    def internal(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        event = _consume_next(events, bindings)
        if not isinstance(event, Callback):
            raise AssertionError(f"expected Callback event, got {event!r}")
        assert event.function is function
        assert _resolve(event.args, bindings) == args
        assert _resolve(event.kwargs, bindings) == kwargs
        result = pair.internal(function, *args, **kwargs)
        _consume_binds(events, bindings)
        return result

    return _gatewaypair.GatewayPair(
        internal=internal,
        external=pair.external,
        sandbox_space=pair.sandbox_space,
        external_space=pair._external_space,
    )
