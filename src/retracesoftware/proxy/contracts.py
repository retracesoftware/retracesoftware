"""Public proxy-layer contracts used to glue runtime layers together.

These protocols are intentionally small. They describe what another layer may
ask the proxy runtime to do; they do not expose how System, GatewayPair,
proxy type generation, binding, or trace I/O implement that behavior.

Do not use these protocols as a reason to probe concrete runtime objects. If a
consumer needs more behavior, add the narrowest new protocol or callable here
after agreeing the contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol

from retracesoftware.gateway._proxytype import Proxy
from retracesoftware.proxy.traceio import TraceReader, TraceWriter


# Callable returned by proxy-constructor factories; this is not a proxy type
# registry or a type-to-type factory.
ProxyConstructor = Callable[[object], Proxy]


@dataclass(frozen=True)
class AsyncCapture:
    """Record-time async event capture policy.

    ``thread_switch`` captures retrace-python scheduler handoffs.
    ``signal`` captures Python signal handler delivery only while internal code
    is running.
    ``gc`` captures observed GC collection start events.
    """

    gc: bool = False
    signal: bool = False
    thread_switch: bool = True


class ProxyRuntime(Protocol):
    """Generate proxy values for external boundary interception.

    CONTRACT LOCKED:
    - ``proxy_type(cls)`` returns the type that should replace ``cls`` in the
      caller-owned namespace.
    - ``patch_function(fn)`` returns the callable that should replace ``fn``.
    - The caller owns namespace replacement and uninstall bookkeeping.
    - Consumers must not inspect the concrete proxy runtime implementation.
    - Consumers must not infer proxy, gateway, binding, or trace semantics from
      the returned callable's concrete type or private attributes.
    """

    def proxy_type(self, cls: type) -> type:
        ...

    def patch_function(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        ...


class Binder(Protocol):
    """Register objects that are already represented in the trace.

    CONTRACT LOCKED:
    - ``bind(obj)`` gives the proxy runtime a stable semantic handle for
      ``obj`` without installing automatic cleanup detection.
    - ``autobind(obj)`` gives ``obj`` a stable semantic handle and arranges
      for cleanup to be detected automatically when the object is collected.
    - ``unbind(obj)`` removes ``obj``'s binding and emits the configured delete
      notification if the object was bound.
    - Calling the binder with ``obj`` returns the stable handle when ``obj`` is
      bound, otherwise it returns ``obj`` unchanged.
    - Consumers must not inspect how handles are allocated or stored.
    - Binding is explicit; consumers must not infer binding from proxy
      concrete types or private attributes.
    """

    def bind(self, obj: object) -> None:
        ...

    def autobind(self, obj: object) -> None:
        ...

    def unbind(self, obj: object) -> None:
        ...

    def __call__(self, obj: object) -> Any:
        ...


class ImmutableRegistry(Protocol):
    """Declare types that cross the boundary unchanged.

    CONTRACT LOCKED:
    - ``add_immutable_type(cls)`` marks instances of ``cls`` as immutable
      passthrough values for this runtime.
    - Consumers must not mutate the registry storage directly.
    """

    def add_immutable_type(self, cls: type) -> None:
        ...

    def add_immutable_types(self, *classes: type) -> None:
        ...


class Checkpoint(Protocol):
    """Record or validate a replay checkpoint for one application value.

    CONTRACT LOCKED:
    - The object is callable with exactly one application value.
    - Record mode writes the checkpoint through the active trace writer.
    - Replay mode validates the value against the next checkpoint message.
    - Replay divergence is reported by the replay runtime's divergence
      exception; consumers should not recover by reading extra trace messages.
    - Consumers must pass semantic application values, not trace-writer
      implementation details.
    """

    def __call__(self, value: Any) -> None:
        ...


class ProxyTypeCustomizer(Protocol):
    """Customize a generated proxy type after creation.

    CONTRACT LOCKED:
    - Called with the semantic type identity used to generate the proxy type:
      ``module``, qualified ``name``, and generated proxy ``cls``.
    - May mutate ``cls`` in place.
    - Must not perform trace I/O, consume replay messages, or inspect
      GatewayPair/System internals.
    - A no-op customizer is valid and is the default.
    """

    def __call__(self, *, module: str, name: str, cls: type) -> None:
        ...


__all__ = [
    "AsyncCapture",
    "Binder",
    "Checkpoint",
    "ImmutableRegistry",
    "ProxyConstructor",
    "ProxyRuntime",
    "ProxyTypeCustomizer",
    "TraceReader",
    "TraceWriter",
]
