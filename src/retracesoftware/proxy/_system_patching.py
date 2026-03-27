"""Type patching helpers for the gate-based proxy system."""

import types

import retracesoftware.utils as utils

from retracesoftware.proxy.typeutils import WithoutFlags
from retracesoftware.proxy.proxytype import superdict


class Patched(utils.Patched):
    """Marker base class for user-defined patched types."""

    __slots__ = ()


def get_all_subtypes(cls):
    """Recursively find all subtypes of a given class."""

    subclasses = set(cls.__subclasses__())
    for subclass in cls.__subclasses__():
        subclasses.update(get_all_subtypes(subclass))
    return subclasses


def patch_type(system, cls):
    """Patch *cls* in-place so its methods route through the gates."""

    assert isinstance(cls, type)
    assert not issubclass(cls, BaseException)

    existing = getattr(cls, "__retrace_system__", None)
    if existing is not None and existing is not system:
        raise RuntimeError(
            f"patch_type: {cls.__qualname__} is already patched by another System instance"
        )

    assert cls not in system.patched_types

    missing = object()
    alloc_patch_undo = None
    patched_attrs = {}
    patched_subtypes = []
    subtype_alloc_undos = []
    subtype_attrs = {}
    original_init_subclass = cls.__dict__.get("__init_subclass__", missing)
    original_retrace = cls.__dict__.get("__retrace__", missing)
    original_retrace_system = cls.__dict__.get("__retrace_system__", missing)
    bound_types = []

    def restore_attr(target, name, original):
        if original is missing:
            if name in target.__dict__:
                delattr(target, name)
        else:
            setattr(target, name, original)

    def bind_patched_type(target):
        system.is_bound.add(target)
        bound_types.append(target)
        system._bind(target)

    def proxy_attrs(target_cls, attr_dict, handler, originals):
        blacklist = system._patch_type_blacklist

        def proxy_function(func):
            return utils.wrapped_function(handler=handler, target=func)

        def proxy_member(member):
            return utils.wrapped_member(handler=handler, target=member)

        for name, value in attr_dict.items():
            if name in blacklist:
                continue
            if name not in originals:
                originals[name] = getattr(target_cls, name)
            if type(value) in [types.MemberDescriptorType, types.GetSetDescriptorType]:
                setattr(target_cls, name, proxy_member(value))
            elif callable(value) and not isinstance(value, type):
                setattr(target_cls, name, proxy_function(value))

    try:
        with WithoutFlags(cls, "Py_TPFLAGS_IMMUTABLETYPE"):
            alloc_patch_undo = utils.set_on_alloc(cls, system._on_alloc)
            system.patched_types.add(cls)

            base_methods = superdict(cls)
            proxy_attrs(cls, attr_dict=base_methods, handler=system._ext_handler, originals=patched_attrs)

            cls.__retrace_system__ = system

            if utils.is_extendable(cls):
                base_method_names = frozenset(base_methods.keys())

                def init_subclass(subtype, patch_alloc=True, **kwargs):
                    system.patched_types.add(subtype)
                    patched_subtypes.append(subtype)
                    bind_patched_type(subtype)

                    if patch_alloc:
                        alloc_undo = utils.set_on_alloc(subtype, system._on_alloc)
                        subtype_alloc_undos.append(alloc_undo)

                    overrides = {
                        name: value
                        for name, value in subtype.__dict__.items()
                        if name in base_method_names
                    }
                    originals = subtype_attrs.setdefault(subtype, {})
                    proxy_attrs(
                        subtype,
                        attr_dict=overrides,
                        handler=system._override_handler,
                        originals=originals,
                    )

                cls.__init_subclass__ = classmethod(init_subclass)

                for subtype in get_all_subtypes(cls):
                    with WithoutFlags(subtype, "Py_TPFLAGS_IMMUTABLETYPE"):
                        init_subclass(subtype, patch_alloc=False)

            cls.__retrace__ = system

        bind_patched_type(cls)
    except Exception:
        for undo in reversed(subtype_alloc_undos):
            undo()

        if alloc_patch_undo is not None:
            alloc_patch_undo()

        for subtype in reversed(patched_subtypes):
            originals = subtype_attrs.get(subtype, {})
            with WithoutFlags(subtype, "Py_TPFLAGS_IMMUTABLETYPE"):
                for name, original in reversed(list(originals.items())):
                    restore_attr(subtype, name, original)
            system.patched_types.discard(subtype)

        with WithoutFlags(cls, "Py_TPFLAGS_IMMUTABLETYPE"):
            for name, original in reversed(list(patched_attrs.items())):
                restore_attr(cls, name, original)
            restore_attr(cls, "__init_subclass__", original_init_subclass)
            restore_attr(cls, "__retrace_system__", original_retrace_system)
            restore_attr(cls, "__retrace__", original_retrace)

        for bound_type in reversed(bound_types):
            system.is_bound.discard(bound_type)

        system.patched_types.discard(cls)
        raise

    return cls
