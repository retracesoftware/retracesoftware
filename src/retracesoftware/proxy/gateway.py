from asyncio import isfuture
from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import Callable

import retracesoftware.functional as functional
import retracesoftware.utils as utils
from contextlib import contextmanager
from retracesoftware.proxy.proxytype import dynamic_int_proxytype, dynamic_proxytype
from retracesoftware.proxy.system import _run_with_replay, adapter, Patched

class Gates:
    """
    Holds two Gate instances (internal, external) and a disable callable.

    - **external**: Gate for external-origin objects (int→ext). Bound to int2ext.
      When active, intercepts calls from internal code to external functions.
    - **internal**: Gate for internal-origin objects (ext→int): callbacks, subclass
      overrides. Bound to ext2int or a predicate-based wrapper. When active,
      intercepts calls from external code back into user code.
    - **disable**: Callable that disables both gates. Call gates.disable() to turn
      retrace off for this thread (both gates passthrough).

    Enable: with gates.context(internal_executor, external_executor): ...
    Disable: gates.disable()
    """

    __slots__ = ("internal", "external", "disable")

    def disable_for(self, function):
        return functional.sequence(
            self.external.apply_with(None), 
            self.internal.apply_with(None),
            function)

    def __init__(self) -> None:
        self.internal = utils.Gate()
        self.external = utils.Gate()

    @contextmanager
    def context(self, internal_executor, external_executor):
        saved_internal = self.internal.executor
        saved_external = self.external.executor

        try:
            self.internal.executor = internal_executor
            self.external.executor = external_executor
            yield self
        finally:
            self.internal.executor = saved_internal
            self.external.executor = saved_external


def adapter_pair(gates: Gates, int_spec, ext_spec, ext_runner=None):
    """Return (int2ext, ext2int) - the two adapter functions for the gate/specs."""
    function = _run_with_replay(ext_runner) if ext_runner \
        else gates.external.apply_with(None)

    int2ext = adapter(
        function=function,
        proxy_input=int_spec.proxy,
        proxy_output=ext_spec.proxy,
        on_call=ext_spec.on_call,
        on_result=ext_spec.on_result,
        on_error=ext_spec.on_error)

    ext2int = functional.if_then_else(
        gates.external.test(int2ext),
        functional.apply,
        adapter(
            function=gates.external.apply_with(int2ext),
            proxy_input=ext_spec.proxy,
            proxy_output=int_spec.proxy,
            on_call=int_spec.on_call,
            on_result=int_spec.on_result,
            on_error=int_spec.on_error))

    return (int2ext, ext2int)


def create_context(
    gates : Gates,
    int_spec,
    ext_spec,
    ext_runner = None) -> AbstractContextManager[Gates]:

    int2ext, ext2int = adapter_pair(gates, int_spec, ext_spec, ext_runner)

    return gates.context(
        internal_executor=ext2int,
        external_executor=int2ext)


def create_int_spec(
    gates : Gates, 
    proxyfactory : Callable,
    on_int_call : Callable,
    bind : Callable) -> SimpleNamespace:

    def int_proxytype(cls):
        return dynamic_int_proxytype(
            handler = gates.internal,
            cls = cls,
            bind = bind)

    return SimpleNamespace(
        proxy = proxyfactory(gates.disable_for(int_proxytype)),
        on_call = on_int_call,
        on_result = None,
        on_error = None)

def create_ext_spec(
    gates : Gates,
    proxyfactory : Callable,
    sync : Callable,
    on_ext_result : Callable,
    on_ext_error : Callable) -> SimpleNamespace:

    def ext_proxytype(cls):
        proxytype = dynamic_proxytype(handler = gates.external, cls = cls)
        proxytype.__retrace_source__ = 'external'

        if issubclass(cls, Patched):
            patched = cls
        elif cls in self.base_to_patched:
            patched = self.base_to_patched[cls]
        else:
            patched = None

        assert patched == None or patched.__base__ is not object

        if patched:
            # breakpoint()

            patcher = getattr(patched, '__retrace_patch_proxy__', None)
            if patcher: patcher(proxytype)

            # for key,value in patched.__dict__.items():
            #     if callable(value) and key not in ['__new__'] and not hasattr(proxytype, key):
            #         setattr(proxytype, key, value)

        return proxytype

    return SimpleNamespace(
        proxy = proxyfactory(gates.disable_for(ext_proxytype)),
        on_call = sync, on_result = on_ext_result, on_error = on_ext_error)

def record_context(gates : Gates, bind):

    return create_context(
        gates = gates,
        sync = sync,
        int_proxytype = int_proxytype,
        ext_proxytype = ext_proxytype,
        on_int_call = on_int_call, 
        on_ext_result = on_ext_result, 
        on_ext_error = on_ext_error)

    def int_proxytype(cls):
        return gates.disable_for(dynamic_int_proxytype(
                handler = gates.internal,
                cls = cls,
                bind = bind))

    def ext_proxytype(cls):

        proxytype = dynamic_proxytype(handler = gates.external, cls = cls)
        proxytype.__retrace_source__ = 'external'

        if issubclass(cls, Patched):
            patched = cls
        elif cls in self.base_to_patched:
            patched = self.base_to_patched[cls]
        else:
            patched = None

        assert patched == None or patched.__base__ is not object

        if patched:
            # breakpoint()

            patcher = getattr(patched, '__retrace_patch_proxy__', None)
            if patcher: patcher(proxytype)

            # for key,value in patched.__dict__.items():
            #     if callable(value) and key not in ['__new__'] and not hasattr(proxytype, key):
            #         setattr(proxytype, key, value)

        return proxytype

    internal = SimpleNamespace(
                 proxy = proxyfactory(gates.disable_for(int_proxytype)), 
                 on_call = on_int_call)

    external = SimpleNamespace(
                 proxy = proxyfactory(gates.disable_for(ext_proxytype)),
                 on_call = on_ext_call,
                 on_result = on_ext_result,
                 on_error = on_ext_error)

    return create_context(gates, internal = internal, external = external)


class GatewayPair:
    """Base for Recorder; provides gates and executor wiring."""
    pass


class Recorder(GatewayPair):

    def __init__(self, external, internal):
        super().__init__(
            external = SimpleNamespace(
                gate = external,
                proxy = proxyfactory(thread_state.wrap('disabled', self.int_proxytype)),
                on_call = tracer('proxy.int.call', self.on_int_call),
                on_result = tracer('proxy.int.result'),
                on_error = tracer('proxy.int.error')),

            internal = SimpleNamespace(
                apply = thread_state.wrap('external', self.ext_apply),
                proxy = proxyfactory(thread_state.wrap('disabled', self.ext_proxytype)),
                on_call = tracer('proxy.ext.call', self.on_ext_call),
                on_result = tracer('proxy.ext.result'),
                on_error = tracer('proxy.ext.error')))

    def context(self):
        saved_int = self.int_gate.executor
        saved_ext = self.ext_gate.executor

        try:
            self.int_gate.executor = self.int_executor
            self.ext_gate.executor = self.ext_executor

            yield self
        finally:
            self.int_gate.executor = saved_int
            self.ext_gate.executor = saved_ext