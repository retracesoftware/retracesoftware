import retracesoftware.utils as utils

from retracesoftware.proxy.contexts import record_context
from retracesoftware.proxy.system import CallHooks, LifecycleHooks, System, unpatch_type


def make_system(*, on_bind=utils.noop):
    system = System(on_bind=on_bind)
    system.primary_hooks = CallHooks()
    system.secondary_hooks = CallHooks()
    system.lifecycle_hooks = LifecycleHooks(on_start=utils.noop, on_end=utils.noop)
    return system


def test_unpatch_type_restores_wrapped_attrs_and_markers():
    system = make_system()

    class Base:
        def read(self):
            return "base"

    class Sub(Base):
        def read(self):
            return "sub"

    original_base_read = Base.__dict__["read"]
    original_sub_read = Sub.__dict__["read"]

    system.patch_type(Base)

    assert isinstance(Base.__dict__["read"], utils.wrapped_function)
    assert isinstance(Sub.__dict__["read"], utils.wrapped_function)
    assert Base.__dict__["__retrace_system__"] is system
    assert Base.__dict__["__retrace__"] is system
    assert Base in system.patched_types
    assert Sub in system.patched_types
    patched_types_before = set(system.patched_types)

    unpatch_type(Base)

    assert Base.__dict__["read"] is original_base_read
    assert Sub.__dict__["read"] is original_sub_read
    assert "__retrace_system__" not in Base.__dict__
    assert "__retrace__" not in Base.__dict__
    assert system.patched_types == patched_types_before


def test_unpatch_type_restores_inherited_methods_by_deleting_shadow():
    system = make_system()

    class Base:
        def read(self):
            return "base"

    class Sub(Base):
        pass

    system.patch_type(Sub)

    assert isinstance(Sub.__dict__["read"], utils.wrapped_function)
    assert utils.unwrap(Sub.__dict__["read"]) is Base.__dict__["read"]

    unpatch_type(Sub)

    assert "read" not in Sub.__dict__
    assert Sub.read is Base.read


def test_unpatch_type_clears_alloc_hook():
    created = []
    system = make_system()

    class Example:
        def ping(self):
            return "pong"

    class Writer:
        def bind(self, obj):
            created.append(obj)

        def write_call(self, *args, **kwargs):
            return None

        def async_call(self, *args, **kwargs):
            return None

        def write_result(self, value):
            return None

        def write_error(self, exc_type, exc_value, exc_tb):
            return None

    system.patch_type(Example)

    with record_context(system, Writer()):
        Example()

    assert created

    created.clear()
    unpatch_type(Example)

    with record_context(system, Writer()):
        Example()

    assert created == []


def test_system_unpatch_type_updates_system_tracking():
    system = make_system()

    class Base:
        def read(self):
            return "base"

    class Sub(Base):
        def read(self):
            return "sub"

    system.patch_type(Base)

    base_wrapper = Base.__dict__["read"]
    sub_wrapper = Sub.__dict__["read"]

    assert Base in system.patched_types
    assert Sub in system.patched_types
    assert base_wrapper in system.is_bound
    assert sub_wrapper in system.is_bound

    system.unpatch_type(Base)

    assert Base not in system.patched_types
    assert Sub not in system.patched_types
    assert base_wrapper not in system.is_bound
    assert sub_wrapper not in system.is_bound


def test_system_unpatch_type_removes_bind_support(monkeypatch):
    system = make_system()

    class Base:
        def read(self):
            return "base"

    class Sub(Base):
        def read(self):
            return "sub"

    added = []
    removed = []

    original_add = utils.Binder.add_bind_support
    original_remove = utils.Binder.remove_bind_support

    def record_add(cls):
        added.append(cls)
        return original_add(cls)

    def record_remove(cls):
        removed.append(cls)
        return original_remove(cls)

    monkeypatch.setattr(utils.Binder, "add_bind_support", staticmethod(record_add))
    monkeypatch.setattr(utils.Binder, "remove_bind_support", staticmethod(record_remove))

    system.patch_type(Base)
    system.unpatch_type(Base)

    assert Base in added
    assert Sub in added
    assert Base in removed
    assert Sub in removed


def test_system_unpatch_types_clears_all_patched_types():
    system = make_system()

    class First:
        def a(self):
            return 1

    class Second:
        def b(self):
            return 2

    class Child(First):
        def a(self):
            return 3

    system.patch_type(First)
    system.patch_type(Second)

    assert First in system.patched_types
    assert Second in system.patched_types
    assert Child in system.patched_types

    system.unpatch_types()

    assert system.patched_types == set()
    assert not isinstance(First.__dict__["a"], utils.wrapped_function)
    assert not isinstance(Second.__dict__["b"], utils.wrapped_function)
    assert not isinstance(Child.__dict__["a"], utils.wrapped_function)
