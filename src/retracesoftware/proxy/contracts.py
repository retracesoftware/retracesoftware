"""Public proxy-layer contracts used to glue runtime layers together.

These protocols are intentionally small. They describe what another layer may
ask the proxy runtime to do; they do not expose how System2, GatewayPair,
TypePatcher, binding, or trace I/O implement that behavior.

Do not use these protocols as a reason to probe concrete runtime objects. If a
consumer needs more behavior, add the narrowest new protocol or callable here
after agreeing the contract.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, TypeAlias

from retracesoftware.proxy.traceio import TraceReader, TraceWriter


Unpatcher: TypeAlias = Callable[[], None]


class Patcher(Protocol):
    """Install runtime patches for external boundary interception.

    CONTRACT LOCKED:
    - ``patch_type(cls)`` patches ``cls`` and returns an unpatcher callable.
    - The returned unpatcher reverses that patch for ``cls``.
    - ``patch_function(fn)`` returns the callable that should replace ``fn``.
    - Consumers must not inspect the concrete patcher implementation.
    - Consumers must not infer proxy, gateway, binding, or trace semantics from
      the returned callable's concrete type or private attributes.
    """

    def patch_type(self, cls: type) -> Unpatcher:
        ...

    def patch_function(self, fn: Callable[..., Any]) -> Callable[..., Any]:
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
      GatewayPair/System2 internals.
    - A no-op customizer is valid and is the default.
    """

    def __call__(self, *, module: str, name: str, cls: type) -> None:
        ...


__all__ = [
    "Checkpoint",
    "Patcher",
    "ProxyTypeCustomizer",
    "TraceReader",
    "TraceWriter",
    "Unpatcher",
]
