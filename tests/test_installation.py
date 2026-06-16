import os
import pathlib
import sys
import types
import posix

import pytest

import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.install.installation import Installation
from retracesoftware.install.edgecases import (
    coverage_collector_start_tracer,
    pytest_cache_for_config,
    pytest_main_control_env,
    pytest_cache_set,
    pytest_get_config_invocation_dir,
    pytest_register_cleanup_lock_removal,
)
from retracesoftware.install.patcher import patch
from retracesoftware.install.replace import ModuleRefIndex, restore_module_refs
from retracesoftware.modules import ModuleConfigResolver
from retracesoftware.proxy.system import CallHooks, LifecycleHooks, System


def _wrap_marker(target):
    def wrapper(self):
        return ("wrapped", target(self))

    return wrapper


def _system_wrap_marker(target, system):
    def wrapper(self):
        return ("system-wrapped", system, target(self))

    return wrapper


def _system():
    system = System(on_bind=utils.noop)
    system.primary_hooks = CallHooks()
    system.secondary_hooks = CallHooks()
    system.lifecycle_hooks = LifecycleHooks(on_start=utils.noop, on_end=utils.noop)
    system.immutable_types.update({int, float, str, bytes, bool, type, type(None)})
    return system


def test_installation_proxy_replaces_function_and_uninstalls():
    system = _system()
    installation = Installation(system)

    calls = []

    def add(a, b):
        calls.append((a, b))
        return a + b

    namespace = {"__name__": "test_installation_proxy", "add": add}

    proxied = installation.proxy(namespace, "add")

    assert proxied is namespace["add"]
    assert proxied is not add
    assert installation.module_objects[0].name == "add"
    assert installation.module_objects[0].original is add
    assert installation.module_objects[0].current is proxied

    installation.uninstall()

    assert namespace["add"] is add


def test_installation_patch_type_unpatches_on_uninstall():
    system = _system()
    installation = Installation(system)

    class Example:
        def ping(self):
            return 123

    namespace = {"__name__": "test_installation_patch_type", "Example": Example}

    patched = installation.patch_type(namespace, "Example")

    assert patched is Example
    assert Example in system.patched_types
    assert getattr(Example, "__retrace_system__", None) is system

    installation.uninstall()

    assert Example not in system.patched_types
    assert getattr(Example, "__retrace_system__", None) is None


def test_installation_proxy_type_tracks_and_unpatches_type():
    system = _system()
    installation = Installation(system)

    class Example:
        def ping(self):
            return 123

    namespace = {"__name__": "test_installation_proxy_type", "Example": Example}

    proxied = installation.proxy(namespace, "Example")

    assert proxied is Example
    assert Example in system.patched_types
    assert installation.module_objects[0].name == "Example"

    installation.uninstall()

    assert Example not in system.patched_types
    assert getattr(Example, "__retrace_system__", None) is None


def test_installation_can_update_module_refs_and_restore_them_on_uninstall():
    system = _system()
    installation = Installation(system, update_refs=True, module_refs_only=True)

    def add(a, b):
        return a + b

    source = types.ModuleType("test_installation_source")
    source.add = add

    peer = types.ModuleType("test_installation_peer")
    peer.exported = add

    sys.modules[source.__name__] = source
    sys.modules[peer.__name__] = peer

    try:
        proxied = installation.proxy(source, "add")

        assert source.add is proxied
        assert peer.exported is proxied

        installation.uninstall()

        assert source.add is add
        assert peer.exported is add
    finally:
        sys.modules.pop(source.__name__, None)
        sys.modules.pop(peer.__name__, None)


def test_module_ref_index_updates_aliases_across_sequential_replacements():
    def original():
        return "original"

    def first():
        return "first"

    def second():
        return "second"

    source = types.ModuleType("test_module_ref_index_source")
    source.target = original

    peer = types.ModuleType("test_module_ref_index_peer")
    peer.alias = original

    index = ModuleRefIndex([source, peer])

    source.target = first
    first_changes = index.replace(original, first)

    assert source.target is first
    assert peer.alias is first

    source.target = second
    second_changes = index.replace(first, second)

    assert source.target is second
    assert peer.alias is second

    restore_module_refs(second_changes)
    restore_module_refs(first_changes)

    assert source.target is second
    assert peer.alias is original


def test_module_ref_index_refreshes_modules_imported_after_construction():
    def original():
        return "original"

    def replacement():
        return "replacement"

    module = types.ModuleType("test_module_ref_index_late_module")
    module.alias = original

    index = ModuleRefIndex()
    sys.modules[module.__name__] = module
    try:
        changes = index.replace(original, replacement)

        assert module.alias is replacement

        restore_module_refs(changes)

        assert module.alias is original
    finally:
        sys.modules.pop(module.__name__, None)


def test_module_ref_index_global_scope_updates_class_attribute_caches():
    def original():
        return "original"

    def replacement():
        return "replacement"

    class CachedFactory:
        cached = original

    module = types.ModuleType("test_module_ref_index_class_attr_source")
    module.target = original

    index = ModuleRefIndex([module], global_scope=True)

    module.target = replacement
    changes = index.replace(original, replacement)

    try:
        assert module.target is replacement
        assert CachedFactory.cached is replacement
    finally:
        restore_module_refs(changes)

    assert module.target is replacement
    assert CachedFactory.cached is original


def test_installation_context_manager_uninstalls_on_exit():
    system = _system()

    def add(a, b):
        return a + b

    namespace = {"__name__": "test_installation_context_manager", "add": add}

    with Installation(system) as installation:
        proxied = installation.proxy(namespace, "add")
        assert namespace["add"] is proxied
        assert proxied is not add

    assert namespace["add"] is add


def test_patcher_patch_accepts_installation():
    system = _system()

    def add(a, b):
        return a + b

    namespace = {"__name__": "test_patcher_patch_accepts_installation", "add": add}

    undo = patch(namespace, {"disable": ["add"]}, Installation(system))

    assert namespace["add"] is not add

    undo()

    assert namespace["add"] is add


def test_type_attribute_proxy_preserves_method_binding_and_uninstalls():
    system = _system()

    class Example:
        def ping(self, value):
            return value + 1

    original = Example.__dict__["ping"]
    namespace = {"__name__": "test_type_attribute_proxy", "Example": Example}

    undo = patch(
        namespace,
        {"type_attributes": {"Example": {"proxy": ["ping"]}}},
        Installation(system),
    )

    try:
        assert Example().ping(41) == 42
    finally:
        undo()

    assert Example.__dict__["ping"] is original


def test_type_attribute_mixed_directives_all_apply_and_uninstall():
    system = _system()
    calls = []
    system.sync = lambda: calls.append("sync")

    class Example:
        def wake(self):
            calls.append("wake")

        def close(self):
            return "close"

    original_wake = Example.__dict__["wake"]
    original_close = Example.__dict__["close"]
    namespace = {"__name__": "test_type_attribute_mixed", "Example": Example}

    undo = patch(
        namespace,
        {
            "type_attributes": {
                "Example": {
                    "sync": ["wake"],
                    "wrap": {
                        "close": "tests.test_installation._wrap_marker",
                    },
                },
            },
        },
        Installation(system),
    )

    try:
        example = Example()
        system.gate.apply_with("internal", example.wake)()
        assert calls == ["sync", "wake"]
        assert example.close() == ("wrapped", "close")
    finally:
        undo()

    assert Example.__dict__["wake"] is original_wake
    assert Example.__dict__["close"] is original_close


def test_type_attribute_system_wrap_receives_system_and_uninstalls():
    system = _system()

    class Example:
        def ping(self):
            return 123

    original = Example.__dict__["ping"]
    namespace = {"__name__": "test_type_attribute_system_wrap", "Example": Example}

    undo = patch(
        namespace,
        {
            "type_attributes": {
                "Example": {
                    "system_wrap": {
                        "ping": "tests.test_installation._system_wrap_marker",
                    },
                },
            },
        },
        Installation(system),
    )

    try:
        assert Example().ping() == ("system-wrapped", system, 123)
    finally:
        undo()

    assert Example.__dict__["ping"] is original


def test_pathparam_skips_callables_without_inspectable_signature():
    system = _system()
    target = functional.if_then_else(
        lambda path: True,
        lambda path: path,
        lambda path: path,
    )
    namespace = {"__name__": "test_pathparam_no_signature", "target": target}

    undo = patch(
        namespace,
        {"pathparam": {"target": "path"}},
        Installation(system),
        pathpredicate=lambda path: True,
    )

    try:
        assert namespace["target"] is target
    finally:
        undo()


def test_posix_pathparam_uses_first_arg_for_uninspectable_builtin(tmp_path):
    system = _system()
    seen = []
    path = tmp_path / "stamp.txt"
    path.write_text("ok", encoding="utf-8")
    namespace = {"__name__": "posix", "utime": posix.utime}

    undo = patch(
        namespace,
        {"pathparam": {"utime": "path"}},
        Installation(system),
        pathpredicate=lambda path: seen.append(path) or False,
    )

    try:
        namespace["utime"](str(path), None)
        assert seen == [str(path)]
    finally:
        undo()

    assert namespace["utime"] is posix.utime


def test_posix_symlink_pathparam_uses_destination_path(tmp_path):
    system = _system()
    seen = []
    target = tmp_path / "target.txt"
    link = tmp_path / "target-current"
    target.write_text("ok", encoding="utf-8")
    namespace = {"__name__": "posix", "symlink": posix.symlink}

    undo = patch(
        namespace,
        {"pathparam": {"symlink": "dst"}},
        Installation(system),
        pathpredicate=lambda path: seen.append(path) or False,
    )

    try:
        namespace["symlink"](target, link)
        assert seen == [link]
        assert link.is_symlink()
    finally:
        undo()

    assert namespace["symlink"] is posix.symlink


def test_stdlib_config_filters_posix_symlink_by_destination_path():
    cfg = ModuleConfigResolver()["posix"]

    assert cfg["pathparam"]["symlink"] == "dst"


def test_stdlib_config_treats_pwd_struct_passwd_as_immutable():
    cfg = ModuleConfigResolver()["pwd"]

    assert "struct_passwd" in cfg["immutable"]


def test_coverage_collector_config_disables_lifecycle_methods():
    pytest.importorskip("coverage")

    cfg = ModuleConfigResolver()["coverage.collector"]
    collector_config = cfg["type_attributes"]["Collector"]

    assert collector_config["system_wrap"]["_start_tracer"] == (
        "retracesoftware.install.edgecases.coverage_collector_start_tracer"
    )
    assert {"__init__", "start", "stop", "flush_data"}.issubset(
        collector_config["disable"]
    )


def test_coverage_sqldata_config_disables_data_store_methods():
    pytest.importorskip("coverage")

    cfg = ModuleConfigResolver()["coverage.sqldata"]
    data_config = cfg["type_attributes"]["CoverageData"]

    assert {"__init__", "set_context", "add_lines", "write"}.issubset(
        data_config["disable"]
    )


def test_coverage_execfile_config_disables_runner_prepare_methods():
    pytest.importorskip("coverage")

    cfg = ModuleConfigResolver()["coverage.execfile"]
    runner_config = cfg["type_attributes"]["PyRunner"]

    assert {"prepare", "_prepare2"}.issubset(runner_config["disable"])


def test_pytest_terminalwriter_config_disables_width_probe():
    cfg = ModuleConfigResolver()["_pytest._io.terminalwriter"]

    assert "get_terminal_width" in cfg["disable"]


def test_pytest_config_disables_plugin_rewrite_metadata_scan():
    cfg = ModuleConfigResolver()["_pytest.config"]
    config_type = cfg["type_attributes"]["Config"]

    assert cfg["system_wrap"]["get_config"] == (
        "retracesoftware.install.edgecases.pytest_get_config_invocation_dir"
    )
    assert cfg["system_wrap"]["main"] == (
        "retracesoftware.install.edgecases.pytest_main_control_env"
    )
    assert cfg["system_wrap"]["_main"] == (
        "retracesoftware.install.edgecases.pytest_main_control_env"
    )
    assert "_mark_plugins_for_rewrite" in config_type["disable"]


def test_pytest_get_config_invocation_dir_disables_and_restores_getcwd():
    disabled_calls = []
    original_getcwd = os.getcwd

    class FakeSystem:
        def disable_for(self, fn, *, unwrap_args=True):
            def disabled(*args, **kwargs):
                disabled_calls.append((fn, args, kwargs, unwrap_args))
                return "/disabled-cwd"

            return disabled

    def target():
        return pathlib.Path.cwd()

    wrapped = pytest_get_config_invocation_dir(target, FakeSystem())

    assert wrapped() == pathlib.Path("/disabled-cwd")
    assert os.getcwd is original_getcwd
    assert len(disabled_calls) == 1
    assert disabled_calls[0][1:] == ((), {}, False)


def test_pytest_main_control_env_disables_only_pytest_version(monkeypatch):
    normal_calls = []
    disabled_calls = []

    class FakeSystem:
        def disable_for(self, fn, *, unwrap_args=True):
            def disabled(*args, **kwargs):
                disabled_calls.append((fn, args, kwargs, unwrap_args))
                return None

            return disabled

    def fake_putenv(*args):
        normal_calls.append(("putenv", args))

    def fake_unsetenv(*args):
        normal_calls.append(("unsetenv", args))

    environ_type = type(os.environ)
    setitem_globals = environ_type.__setitem__.__globals__
    delitem_globals = environ_type.__delitem__.__globals__
    monkeypatch.setattr(os, "putenv", fake_putenv)
    monkeypatch.setattr(os, "unsetenv", fake_unsetenv)
    monkeypatch.setitem(setitem_globals, "putenv", fake_putenv)
    monkeypatch.setitem(delitem_globals, "unsetenv", fake_unsetenv)

    def target():
        os.environ["PYTEST_VERSION"] = "9.1.0"
        os.environ["PYTEST_CURRENT_TEST"] = "test_sample.py::test_example (call)"
        os.environ["RETRACE_APP_ENV_TEST"] = "kept"
        del os.environ["PYTEST_CURRENT_TEST"]
        del os.environ["PYTEST_VERSION"]
        del os.environ["RETRACE_APP_ENV_TEST"]
        return "done"

    wrapped = pytest_main_control_env(target, FakeSystem())

    assert wrapped() == "done"
    assert normal_calls == [
        ("putenv", (b"RETRACE_APP_ENV_TEST", b"kept")),
        ("unsetenv", (b"RETRACE_APP_ENV_TEST",)),
    ]
    assert [(args, unwrap) for _, args, _, unwrap in disabled_calls] == [
        ((b"PYTEST_VERSION", b"9.1.0"), False),
        ((b"PYTEST_CURRENT_TEST", b"test_sample.py::test_example (call)"), False),
        ((b"PYTEST_CURRENT_TEST",), False),
        ((b"PYTEST_VERSION",), False),
    ]


def test_pytest_timing_config_disables_terminal_duration_timer():
    cfg = ModuleConfigResolver()["_pytest.timing"]
    instant_type = cfg["type_attributes"]["Instant"]

    assert "__init__" in instant_type["disable"]


def test_pytest_logging_config_disables_unconfigure_cleanup():
    cfg = ModuleConfigResolver()["_pytest.logging"]
    logging_plugin_type = cfg["type_attributes"]["LoggingPlugin"]

    assert "pytest_unconfigure" in logging_plugin_type["disable"]


def test_pytest_pathlib_config_disables_tempdir_cleanup_pid_probe():
    cfg = ModuleConfigResolver()["_pytest.pathlib"]

    assert "cleanup_numbered_dir" in cfg["disable"]
    assert cfg["system_wrap"]["register_cleanup_lock_removal"] == (
        "retracesoftware.install.edgecases.pytest_register_cleanup_lock_removal"
    )


def test_pytest_cacheprovider_config_disables_builtin_sessionfinish_writes():
    cfg = ModuleConfigResolver()["_pytest.cacheprovider"]

    assert "pytest_sessionfinish" in cfg["type_attributes"]["LFPlugin"]["disable"]
    assert "pytest_sessionfinish" in cfg["type_attributes"]["NFPlugin"]["disable"]


def test_stdlib_config_records_time_sleep_boundary():
    cfg = ModuleConfigResolver()["time"]

    assert "sleep" in cfg["proxy"]


def test_py_process_forkedfunc_config_wraps_waitfinish():
    pytest.importorskip("py._process.forkedfunc")

    cfg = ModuleConfigResolver()["py._process.forkedfunc"]
    forked_func_type = cfg["type_attributes"]["ForkedFunc"]

    assert forked_func_type["system_wrap"]["waitfinish"] == (
        "retracesoftware.install.edgecases.py_forkedfunc_waitfinish"
    )


def test_py_path_local_config_wraps_mkdtemp():
    pytest.importorskip("py._path.local")

    cfg = ModuleConfigResolver()["py._path.local"]
    local_path_type = cfg["type_attributes"]["LocalPath"]

    assert local_path_type["system_wrap"]["mkdtemp"] == (
        "retracesoftware.install.edgecases.py_localpath_mkdtemp"
    )


def test_pytest_cache_set_runs_target_with_retrace_disabled():
    calls = []

    class FakeSystem:
        def disable_for(self, fn, *, unwrap_args=True):
            def disabled(*args, **kwargs):
                calls.append(("disabled", fn, args, kwargs, unwrap_args))
                return fn(*args, **kwargs)

            return disabled

        def patch_function(self, fn):
            def patched(*args, **kwargs):
                calls.append(("patched", fn, args, kwargs))
                return fn(*args, **kwargs)

            return patched

    cache = object()

    def target(self, key, value):
        calls.append(("target", self, key, value))
        return f"{key}:{value}"

    wrapped = pytest_cache_set(target, FakeSystem())

    assert wrapped(cache, "randomly_seed", 12345) == "randomly_seed:12345"
    assert calls[0][0] == "patched"
    assert calls[0][2] == ("randomly_seed", 0)
    assert calls[1] == (
        "disabled",
        target,
        (cache, "randomly_seed", 12345),
        {},
        False,
    )
    assert calls[2] == ("target", cache, "randomly_seed", 12345)


def test_pytest_cache_for_config_edgecase_binds_cacheclear_probe_lazily(tmp_path):
    calls = []

    class FakeSystem:
        def patch_function(self, fn):
            calls.append(fn)
            return fn

    class Config:
        def __init__(self, cacheclear):
            self.cacheclear = cacheclear

        def getoption(self, name):
            assert name == "cacheclear"
            return self.cacheclear

    class Cache:
        cleared = []

        def __init__(self, cachedir, config, *, _ispytest=False):
            self.cachedir = cachedir
            self.config = config
            self._ispytest = _ispytest

        @classmethod
        def for_config(cls, config, *, _ispytest=False):
            return cls(tmp_path / "default-cache", config, _ispytest=_ispytest)

        @classmethod
        def cache_dir_from_config(cls, config, *, _ispytest=False):
            return tmp_path / "existing-cache"

        @classmethod
        def clear_cache(cls, cachedir, *, _ispytest=False):
            cls.cleared.append((cachedir, _ispytest))

    cachedir = tmp_path / "existing-cache"
    cachedir.mkdir()
    wrapped = pytest_cache_for_config(Cache.for_config, FakeSystem())

    assert calls == []
    no_clear = wrapped(Config(False), _ispytest=True)
    assert calls == []
    assert no_clear.cachedir == tmp_path / "default-cache"

    with_clear = wrapped(Config(True), _ispytest=True)
    assert len(calls) == 1
    assert with_clear.cachedir == cachedir
    assert Cache.cleared == [(cachedir, True)]


def test_pytest_register_cleanup_lock_removal_disables_registered_callback(tmp_path):
    disabled_calls = []
    registered = []

    class FakeSystem:
        def disable_for(self, fn, *, unwrap_args=True):
            def disabled(*args, **kwargs):
                disabled_calls.append((fn, args, kwargs, unwrap_args))
                return fn(*args, **kwargs)

            return disabled

    def target(lock_path, register):
        def cleanup():
            return "cleaned"

        return register(cleanup)

    def register(callback):
        registered.append(callback)
        return "registered"

    wrapped = pytest_register_cleanup_lock_removal(target, FakeSystem())

    assert wrapped(tmp_path / ".lock", register) == "registered"
    assert len(registered) == 1
    assert registered[0]() == "cleaned"
    assert len(disabled_calls) == 2
    assert disabled_calls[0][1][0] == tmp_path / ".lock"
    assert callable(disabled_calls[0][1][1])
    assert disabled_calls[0][2:] == ({}, False)
    assert disabled_calls[1][1:] == ((), {}, False)


def test_pytest_register_cleanup_lock_removal_preserves_default_register(tmp_path):
    disabled_calls = []
    registered = []

    class FakeSystem:
        def disable_for(self, fn, *, unwrap_args=True):
            def disabled(*args, **kwargs):
                disabled_calls.append((fn, args, kwargs, unwrap_args))
                return fn(*args, **kwargs)

            return disabled

    def default_register(callback):
        registered.append(callback)
        return "default-registered"

    def target(lock_path, register=default_register):
        def cleanup():
            return lock_path.name

        return register(cleanup)

    wrapped = pytest_register_cleanup_lock_removal(target, FakeSystem())

    assert wrapped(tmp_path / ".lock") == "default-registered"
    assert len(registered) == 1
    assert registered[0]() == ".lock"
    assert len(disabled_calls) == 2
    assert disabled_calls[0][1][0] == tmp_path / ".lock"
    assert callable(disabled_calls[0][1][1])
    assert disabled_calls[0][2:] == ({}, False)
    assert disabled_calls[1][1:] == ((), {}, False)


def test_pytest_register_cleanup_lock_removal_wraps_keyword_register(tmp_path):
    disabled_calls = []
    registered = []

    class FakeSystem:
        def disable_for(self, fn, *, unwrap_args=True):
            def disabled(*args, **kwargs):
                disabled_calls.append((fn, args, kwargs, unwrap_args))
                return fn(*args, **kwargs)

            return disabled

    def target(lock_path, *, register):
        def cleanup():
            return lock_path.name

        return register(cleanup)

    def register(callback):
        registered.append(callback)
        return "keyword-registered"

    wrapped = pytest_register_cleanup_lock_removal(target, FakeSystem())

    assert wrapped(tmp_path / ".lock", register=register) == "keyword-registered"
    assert len(registered) == 1
    assert registered[0]() == ".lock"
    assert len(disabled_calls) == 2
    assert disabled_calls[0][1][0] == tmp_path / ".lock"
    assert callable(disabled_calls[0][2]["register"])
    assert disabled_calls[0][3] is False
    assert disabled_calls[1][1:] == ((), {}, False)


def test_pluggy_manager_config_disables_pytest_entrypoint_scan():
    pytest.importorskip("pluggy")

    cfg = ModuleConfigResolver()["pluggy._manager"]
    manager_type = cfg["type_attributes"]["PluginManager"]

    assert "load_setuptools_entrypoints" in manager_type["disable"]


def test_coverage_collector_start_tracer_disables_tracer_callbacks():
    system = _system()

    class Tracer:
        pass

    class Collector:
        def __init__(self):
            self.tracers = []
            self.original_gate = "not-called"

    def original_start_tracer(collector):
        collector.original_gate = system.gate.get()
        tracer = Tracer()
        tracer.should_trace = lambda filename, frame: ("should_trace", system.gate.get())
        tracer.switch_context = lambda context: ("switch_context", system.gate.get())
        tracer.lock_data = lambda: ("lock_data", system.gate.get())
        collector.tracers.append(tracer)
        return "trace-function"

    collector = Collector()
    wrapped = coverage_collector_start_tracer(original_start_tracer, system)

    result = system.gate.apply_with("internal", wrapped)(collector)

    assert result == "trace-function"
    assert collector.original_gate is None
    tracer = collector.tracers[0]
    assert getattr(tracer.should_trace, "__retrace_disabled_thread_target__", False)
    assert system.gate.apply_with("internal", tracer.should_trace)("example.py", None) == (
        "should_trace",
        None,
    )
    assert system.gate.apply_with("internal", tracer.switch_context)("ctx") == (
        "switch_context",
        None,
    )
    assert system.gate.apply_with("internal", tracer.lock_data)() == ("lock_data", None)
