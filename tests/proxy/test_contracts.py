from collections.abc import Callable as CallableABC
from typing import get_args, get_origin, get_type_hints

import retracesoftware.proxy.contracts as contracts
from retracesoftware.gateway._proxytype import Proxy
from retracesoftware.proxy.contracts import (
    AsyncCapture,
    Binder,
    Checkpoint,
    ImmutableRegistry,
    ProxyConstructor,
    ProxyRuntime,
    ProxyTypeCustomizer,
    TraceReader,
    TraceWriter,
)
from retracesoftware.proxy.system import System
from retracesoftware.proxy.traceio import TraceReader as TraceReaderContract
from retracesoftware.proxy.traceio import TraceWriter as TraceWriterContract


def test_proxy_runtime_contract_exports_generation_methods():
    assert hasattr(ProxyRuntime, "proxy_type")
    assert hasattr(ProxyRuntime, "patch_function")


def test_proxy_constructor_contract_shape():
    args, return_type = get_args(ProxyConstructor)

    assert contracts.ProxyConstructor is ProxyConstructor
    assert "ProxyConstructor" in contracts.__all__
    assert get_origin(ProxyConstructor) is CallableABC
    assert args == [object]
    assert return_type is Proxy


def test_binder_contract_exports_bind_method():
    assert hasattr(Binder, "bind")


def test_immutable_registry_contract_exports_add_methods():
    assert hasattr(ImmutableRegistry, "add_immutable_type")
    assert hasattr(ImmutableRegistry, "add_immutable_types")


def test_trace_io_contracts_are_exported_from_contracts_module():
    assert TraceReader is TraceReaderContract
    assert TraceWriter is TraceWriterContract


def test_async_capture_defaults_to_thread_switch_only():
    capture = AsyncCapture()

    assert capture.thread_switch is True
    assert capture.signal is False
    assert capture.gc is False


def test_proxy_type_customizer_contract_shape():
    hints = get_type_hints(ProxyTypeCustomizer.__call__)

    assert hints["module"] is str
    assert hints["name"] is str
    assert hints["cls"] is type
    assert hints["return"] is type(None)


def test_checkpoint_contract_is_single_value_callable():
    hints = get_type_hints(Checkpoint.__call__)

    assert "value" in hints
    assert hints["return"] is type(None)


def test_system_declares_proxy_runtime_contract_and_checkpoint_member():
    hints = get_type_hints(System)

    assert ProxyRuntime in System.__mro__
    assert Binder in System.__mro__
    assert ImmutableRegistry in System.__mro__
    assert hints["checkpoint"] is Checkpoint
