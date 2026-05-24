# GatewayPair Design

`GatewayPair` is the small boundary kernel between deterministic application
code and external code.  It owns the two coordinate-space entry points and the
value transformations that happen when a call crosses the boundary.

The goal is to move boundary behavior out of the larger proxy `System`, where
record/replay, proxy creation, binding, observation, patching, lifecycle, and
thread concerns were all mixed together.

## Public Interface

The only public gateway interface is:

```python
from retracesoftware.gateway import GatewayPair

pair = GatewayPair.create_recording_pair(...)
pair = GatewayPair.create_replay_pair(...)

pair.external(function, *args, **kwargs)
pair.internal(function, *args, **kwargs)
pair.sandbox_space
```

`pair.external(...)` is the internal-to-external entry point.

`pair.internal(...)` is the external-to-internal callback entry point.

`pair.sandbox_space` is the coordinate space used by application/sandbox code.
It exists so adjacent runtime code can install scheduling observers for the
right coordinate space without making `GatewayPair` own scheduling or trace I/O.

All other modules and helpers under `retracesoftware.gateway` are private
implementation or test-support details.

## Responsibilities

`GatewayPair` owns:

- creation of the internal and external coordinate spaces
- creation of the internal and external dispatch gateways
- creation of internal and external dynamic proxy values
- creation of internal and external dynamic proxy types
- internal-to-external call routing
- external-to-internal callback routing
- record-time result/error/callback observation hooks
- replay-time replacement of external calls with recorded results/errors
- proxying/unwrapping values at the boundary
- binding callbacks supplied by the factory

`GatewayPair` does not own:

- trace formats
- trace readers/writers
- thread-switch recording
- signal or GC callback recording
- patching/unpatching runtime types
- install/session lifecycle
- debugger/control-plane I/O

Those concerns should compose around `GatewayPair`, not be added to it.

## Dynamic Proxies

The gateway boundary cannot pass arbitrary live objects across unchanged.
Objects crossing the boundary often need a proxy shell whose methods route back
through the opposite gateway.

There are two proxy directions:

- internal proxies represent external/live objects inside sandbox code
- external proxies represent sandbox/internal callback objects to external code

The names describe where the proxy is used, not where the original object was
created.

### Internal Proxies

An internal proxy is returned to sandbox code when an external value must stay
observable.

Example:

```text
sandbox code -> pair.external(open, ...)
external open returns file object
gateway creates internal proxy for that file
sandbox code receives proxy
proxy methods route through the external gateway
```

The internal proxy type is generated from the concrete source type.  Its method
descriptors are wrapped with the internal gateway so that later method calls
cross the same boundary machinery instead of escaping directly to the live
object.

During replay, external calls do not run.  However, replay still runs external
call inputs through the internal proxy path.  This matters because proxy
creation can create bindings that later recorded events refer to.

### External Proxies

An external proxy is passed to live external code when sandbox objects or
callback objects cross outward.

Example:

```text
sandbox subclass instance is passed to external method
external method later calls instance.callback(...)
external proxy method routes callback through pair.internal(...)
```

The external proxy type is generated from a type spec: module, qualified name,
method names, attribute names, and attribute-behavior flags.  Methods on that
proxy type are wrapped with the external gateway so that calls from live code
can re-enter sandbox code as callbacks.

### Proxy Type Creation

Proxy type creation is itself boundary-sensitive.

When a new external proxy type is needed, `GatewayPair` creates an
`ext_proxytype_from_spec` callable and wraps it in the sandbox coordinate
space.  Calls to that helper can be bound and observed as callback-like work, so
record and replay can recreate proxy classes at the corresponding point.

Created proxy types and proxy references are passed to `bind(value)` so the
binding layer can assign stable handles.  That is why `bind` is part of the
factory contract rather than an implementation detail hidden inside proxy
construction.

The surrounding runtime may also pass a proxy type customizer.  The customizer
is called with `module`, qualified `name`, and generated proxy `cls` after a
proxy type is created.  It is intentionally just a callback: GatewayPair owns
proxy type creation, while installer-specific edgecase policy remains outside
the gateway package.

### Record And Replay Differences

Recording may create both internal and external proxies from live values:

- external call arguments use the internal proxy path
- external call results use the external proxy path when the passthrough
  predicate returns `False`
- callback inputs use the external proxy path
- callback results use the internal proxy path

Replay must not create external proxies from live external results, because the
live external call must not run.  Replay external-call results come from
`next_result`.  If replay ever tries to create an external proxy from a live
value, that is an illegal state.

Replay may still create internal proxies for input values.  This is intentional:
those proxy creations can establish bindings needed by later replay events.

## Binding

`bind(value)` tells the surrounding recording/replay layer that `value` must be
addressable by a stable handle.

The gateway does not choose the trace representation for those handles.  It only
knows that certain runtime values must be registered before later events can
refer to them.

Binding is needed for values whose identity matters across events:

- generated proxy types
- proxy references
- internal proxy instances created for external/live objects
- wrapped callables that route generated proxy methods through a gateway
- callback objects that may be referenced again later

Without binding, a later result or callback argument would have to carry the
entire object again, or worse, rely on concrete object identity from the current
process.  Binding gives the trace layer a semantic reference point:

```text
Bind(handle=7, value=file_proxy)
Result(value=Bound(handle=7))
Callback(args=(Bound(handle=7),), kwargs={})
```

### Record-Time Binding

During recording, `bind(value)` should allocate or discover a handle for
`value`, then make that handle available to subsequent result/callback events.

GatewayPair calls `bind` when it creates values that may need stable identity.
The caller decides what `bind` means:

- production trace code may emit bind-open/bind-close messages
- tests may store `{handle: value}` in a dict
- debugging tools may simply collect the bound values

### Replay-Time Binding

During replay, bind events recreate the handle table before recorded results or
callbacks are resolved.  `Bound(handle)` then resolves through that table.

Replay may also create internal proxies for external-call inputs, even though
the external call itself is not run.  This preserves binding side effects for
objects that later events may reference.

### Binding In Gateway Tests

The private list recorder models binding with:

- `Bind(handle, value)`
- `Bound(handle)`
- a plain dictionary `{handle: value}`

Recording assigns handles and encodes repeated bound values as `Bound(handle)`.
Replay consumes `Bind` events into a fresh dict and resolves `Bound(handle)` in
`Result` and `Callback` payloads.

This model is intentionally tiny.  It proves the gateway contract without
depending on the production trace format.

## Recording Semantics

`GatewayPair.create_recording_pair(...)` accepts:

- `is_passthrough(value) -> bool`
- optional `unwrap(value) -> value` (defaults to `utils.try_unwrap`)
- `on_callback(function, *args, **kwargs)`
- `on_error(exc_type, exc_value, traceback)`
- `on_result(value)`
- `bind(value)`
- optional `internal_space`
- optional `external_space`

`is_passthrough` is a predicate, not a boolean flag.  It is called with values
that may need proxying.  It returns `True` only when the value can cross the
boundary unchanged.  It returns `False` when the value must be proxied.

### Internal To External

When sandbox code calls `pair.external(function, *args, **kwargs)`:

1. The first argument, `function`, is unwrapped.
2. Remaining args and kwargs are unwrapped first, then transformed through the
   internal proxy path if they still cannot pass through.
3. The live external function runs in the external coordinate space.
4. If the call returns:
   - passthrough results are observed unchanged
   - non-passthrough results are converted through the external proxy path, then observed
5. The observed value is returned to sandbox code; non-passthrough results
   remain proxied so later use crosses the gateway too.
6. If the call raises, `on_error` is called as a side effect and the original exception is re-raised.

External call inputs are not observed as trace events by this layer.

### External To Internal Callback

When external code calls `pair.internal(function, *args, **kwargs)`:

1. The first argument, `function`, is unwrapped.
2. Remaining args and kwargs are transformed through the external proxy path.
3. `on_callback(function, *args, **kwargs)` observes the callback invocation.
4. The callback runs in the sandbox coordinate space.
5. The callback result is transformed back through the internal proxy path for external code.

Callback result and callback error are not observed by the recording pair.

## Replay Semantics

`GatewayPair.create_replay_pair(...)` accepts:

- `is_passthrough(value) -> bool`
- `next_result(*args, **kwargs)`
- `bind(value)`
- optional `internal_space`
- optional `external_space`

### Internal To External

Replay must not call live external code.

When sandbox code calls `pair.external(function, *args, **kwargs)`:

1. The first argument is replaced with a callable that reads the next recorded result/error.
2. Remaining args and kwargs still run through the internal proxy path.
3. This preserves binding side effects for input values even though the live external function is not called.
4. The recorded result is returned, or the recorded error is raised.

### External To Internal Callback

When replayed external code calls `pair.internal(function, *args, **kwargs)`:

1. The first argument, `function`, is unwrapped.
2. Remaining args and kwargs are unwrapped into sandbox values.
3. The callback runs in the sandbox coordinate space.
4. The callback result is transformed through the internal proxy path for external code.

## Binding Test Support

The private `_recording` module provides in-memory test machinery:

```python
events = []
bindings = {}

record_pair = create_recording_pair_recorder(
    record=events.append,
    bindings=bindings,
    is_passthrough=predicate,
)

replay_pair = create_replay_pair_recorder(
    events=list(events),
    bindings={},
    is_passthrough=predicate,
)
```

It emits simple dataclass events:

- `Bind(handle, value)`
- `Bound(handle)`
- `Result(value)`
- `Error(exc_type, exc_value, traceback)`
- `Callback(function, args, kwargs)`

Recording assigns integer handles in a dict and encodes later event payloads
with `Bound(handle)` when a previously bound value appears again.

Replay consumes `Bind` events into its own dict and resolves `Bound(handle)` in
results and callbacks.

This is test scaffolding, not a trace format.

## Test Matrix

Gateway tests should cover these contracts in-process, without subprocesses:

| Area | Required Coverage |
| --- | --- |
| Public surface | `retracesoftware.gateway` exports only `GatewayPair` |
| Sandbox space | `pair.external(...)` enters through `pair.sandbox_space` |
| Recording external result | live target runs, rest args/kwargs are proxied, result is observed |
| Recording passthrough | predicate is called with result; passthrough result is not proxied |
| Recording non-passthrough | predicate is called once; false result is proxied before observation |
| Recording external error | `on_error` observes original exception; original exception propagates |
| Recording callback | callback invocation is observed with function, args, kwargs |
| Recording callback result | callback result is returned through internal proxy path, but not observed as `Result` |
| Recording callback error | callback error propagates, but is not observed as external-call `Error` |
| Replay external result | live target is not called; recorded result is returned |
| Replay external error | live target is not called; recorded error is raised |
| Replay external inputs | inputs are still proxied so binding side effects occur |
| Replay callback | callback function runs in sandbox space; result is proxied back out |
| Binding record | `Bind(handle, value)` is emitted and bindings dict is populated |
| Binding replay | `Bind` is consumed and `Bound(handle)` resolves in results |
| Callback binding replay | `Bound(handle)` resolves in callback args/kwargs |
| Ordering | replay consumes events in order and fails on unexpected event kind |

The matrix is intentionally about gateway behavior only.  Thread switches,
trace serialization, patch installation, and process replay belong to their
own component tests.
