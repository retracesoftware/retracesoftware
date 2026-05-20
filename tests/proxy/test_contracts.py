from typing import get_type_hints

from retracesoftware.proxy.contracts import (
    Checkpoint,
    Patcher,
    ProxyTypeCustomizer,
    TraceReader,
    TraceWriter,
    Unpatcher,
)
from retracesoftware.proxy.system2 import System2
from retracesoftware.proxy.traceio import TraceReader as TraceReaderContract
from retracesoftware.proxy.traceio import TraceWriter as TraceWriterContract


def test_patcher_contract_exports_patch_methods():
    assert hasattr(Patcher, "patch_type")
    assert hasattr(Patcher, "patch_function")
    assert Unpatcher is not None


def test_trace_io_contracts_are_exported_from_contracts_module():
    assert TraceReader is TraceReaderContract
    assert TraceWriter is TraceWriterContract


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


def test_system2_declares_patcher_contract_and_checkpoint_member():
    hints = get_type_hints(System2)

    assert Patcher in System2.__mro__
    assert hints["checkpoint"] is Checkpoint
