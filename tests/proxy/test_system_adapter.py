import pytest

import retracesoftware.functional as functional
import retracesoftware.proxy.system as system_mod
import retracesoftware.utils as utils
from retracesoftware.proxy.stubfactory import StubRef
from retracesoftware.proxy.messagestream import MemoryWriter


@pytest.fixture(autouse=True)
def adapter_runtime_shims(monkeypatch):
    monkeypatch.setattr(
        system_mod.functional,
        "when_not",
        lambda predicate, slow_path: lambda value: value if predicate(value) else slow_path(value),
        raising=False,
    )
    monkeypatch.setattr(system_mod.utils, "side_effect", functional.side_effect, raising=False)


def test_adapter_passthrough_skips_proxy_and_unproxy_without_observers():
    events = []

    def passthrough(_value):
        return True

    def proxy_input(value):
        events.append(("proxy_input", value))
        return f"proxy:{value}"

    def unproxy_input(value):
        events.append(("unproxy_input", value))
        return value.removeprefix("proxy:")

    def proxy_output(value):
        events.append(("proxy_output", value))
        return f"proxy-out:{value}"

    def unproxy_output(value):
        events.append(("unproxy_output", value))
        return value.removeprefix("proxy-out:")

    def function(_fn, arg):
        events.append(("function", arg))
        return f"result:{arg}"

    wrapped = system_mod.adapter(
        function=function,
        passthrough=passthrough,
        proxy_input=proxy_input,
        unproxy_input=unproxy_input,
        proxy_output=proxy_output,
        unproxy_output=unproxy_output,
    )

    assert wrapped(object(), "value") == "result:value"
    assert events == [("function", "value")]


def test_adapter_records_proxied_input_then_calls_function_with_unproxied_value():
    events = []

    def passthrough(_value):
        return False

    def proxy_input(value):
        events.append(("proxy_input", value))
        return f"proxy:{value}"

    def unproxy_input(value):
        events.append(("unproxy_input", value))
        return value.removeprefix("proxy:")

    def proxy_output(value):
        events.append(("proxy_output", value))
        return f"proxy-out:{value}"

    def unproxy_output(value):
        events.append(("unproxy_output", value))
        return value.removeprefix("proxy-out:")

    def on_call(_fn, arg):
        events.append(("write_call", arg))

    def on_result(value):
        events.append(("write_result", value))

    def function(_fn, arg):
        events.append(("function", arg))
        return f"result:{arg}"

    wrapped = system_mod.adapter(
        function=function,
        passthrough=passthrough,
        proxy_input=proxy_input,
        unproxy_input=unproxy_input,
        proxy_output=proxy_output,
        unproxy_output=unproxy_output,
        on_call=on_call,
        on_result=on_result,
    )

    assert wrapped(object(), "value") == "result:value"
    assert events == [
        ("proxy_input", "value"),
        ("write_call", "proxy:value"),
        ("unproxy_input", "proxy:value"),
        ("function", "value"),
        ("proxy_output", "result:value"),
        ("write_result", "proxy-out:result:value"),
        ("unproxy_output", "proxy-out:result:value"),
    ]


def test_adapter_passthrough_writes_raw_result_without_proxying_output():
    events = []

    def passthrough(_value):
        return True

    def proxy_input(value):
        events.append(("proxy_input", value))
        return f"proxy:{value}"

    def unproxy_input(value):
        events.append(("unproxy_input", value))
        return value.removeprefix("proxy:")

    def proxy_output(value):
        events.append(("proxy_output", value))
        return f"proxy-out:{value}"

    def unproxy_output(value):
        events.append(("unproxy_output", value))
        return value.removeprefix("proxy-out:")

    def on_call(_fn, arg):
        events.append(("write_call", arg))

    def on_result(value):
        events.append(("write_result", value))

    def function(_fn, arg):
        events.append(("function", arg))
        return f"result:{arg}"

    wrapped = system_mod.adapter(
        function=function,
        passthrough=passthrough,
        proxy_input=proxy_input,
        unproxy_input=unproxy_input,
        proxy_output=proxy_output,
        unproxy_output=unproxy_output,
        on_call=on_call,
        on_result=on_result,
    )

    assert wrapped(object(), "value") == "result:value"
    assert events == [
        ("proxy_input", "value"),
        ("write_call", "proxy:value"),
        ("unproxy_input", "proxy:value"),
        ("function", "value"),
        ("write_result", "result:value"),
    ]


def test_record_context_external_method_body_sees_wrapped_argument():
    seen = {}

    class Payload:
        pass

    class Host:
        def inspect(self, other):
            seen["is_wrapped"] = utils.is_wrapped(other)
            seen["is_internal_wrapped"] = isinstance(other, utils.InternalWrapped)
            seen["unwraps_to_payload"] = utils.unwrap(other) is payload
            return Payload()

    system = system_mod.System()
    system.immutable_types.update({str, bool, int, type(None)})
    system.patch_type(Host)

    payload = Payload()
    class WriterSpy(MemoryWriter):
        def bind(self, obj):
            seen.setdefault("bound_objects", []).append(obj)
            seen.setdefault("bind_calls", []).append({
                "is_wrapped": utils.is_wrapped(obj),
                "is_internal_wrapped": isinstance(obj, utils.InternalWrapped),
                "unwraps_to_payload": utils.is_wrapped(obj) and utils.unwrap(obj) is payload,
            })
            super().bind(obj)

        def write_result(self, value):
            seen["written_is_wrapped"] = utils.is_wrapped(value)
            seen["written_is_external_wrapped"] = isinstance(value, utils.ExternalWrapped)
            seen["written_unwraps_to_payload"] = type(utils.unwrap(value)) is Payload
            return super().write_result(value)

    writer = WriterSpy()

    with system.record_context(writer):
        host = Host()
        result = host.inspect(payload)

    assert utils.is_wrapped(result)
    assert isinstance(result, utils.ExternalWrapped)
    assert type(utils.unwrap(result)) is Payload

    assert seen["bound_objects"][0] is host
    assert isinstance(seen["bound_objects"][1], StubRef)
    assert seen["bind_calls"] == [
        {
            "is_wrapped": False,
            "is_internal_wrapped": False,
            "unwraps_to_payload": False,
        },
        {
            "is_wrapped": False,
            "is_internal_wrapped": False,
            "unwraps_to_payload": False,
        },
    ]

    assert seen == {
        "is_wrapped": True,
        "is_internal_wrapped": True,
        "unwraps_to_payload": True,
        "bound_objects": seen["bound_objects"],
        "bind_calls": seen["bind_calls"],
        "written_is_wrapped": True,
        "written_is_external_wrapped": True,
        "written_unwraps_to_payload": True,
    }
