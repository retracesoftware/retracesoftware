import types

import retracesoftware.stream as stream
import retracesoftware.utils as utils

from retracesoftware.proxy.proxytype import superdict
from retracesoftware.proxy.typeutils import WithoutFlags

_MISSING = object()

def get_all_subtypes(cls):
    """Recursively find all subtypes of a given class."""

    subclasses = set(cls.__subclasses__())
    for subclass in cls.__subclasses__():
        subclasses.update(get_all_subtypes(subclass))
    return subclasses

def _restore_attr(target, name, original):
    if original is _MISSING:
        if name in target.__dict__:
            delattr(target, name)
    else:
        setattr(target, name, original)

def _unwrap_patched_attr(cls, name, value):
    target = utils.unwrap(value)

    for base in cls.__mro__[1:]:
        if base.__dict__.get(name) is target:
            delattr(cls, name)
            return value

    setattr(cls, name, target)
    return value

def unpatch_type(cls):
    assert isinstance(cls, type)

    system = getattr(cls, "__retrace_system__", None)

    for subtype in tuple(cls.__subclasses__()):
        if getattr(subtype, "__retrace_system__", None) is system:
            unpatch_type(subtype)

    _unpatch_type_one(cls)

    return cls

_module_unpatch_type = unpatch_type

def _is_patch_generated_init_subclass(value):
    if not isinstance(value, classmethod):
        return False

    func = value.__func__
    code = getattr(func, "__code__", None)
    if code is None or func.__name__ != "init_subclass":
        return False

    freevars = set(code.co_freevars)
    return {"system", "proxy_attrs", "subtype_attrs"} <= freevars

def _unpatch_type_one(
    cls,
    *,
    original_attrs=None,
    original_init_subclass=_MISSING,
    original_retrace_system=_MISSING,
    original_retrace=_MISSING,
):
    with WithoutFlags(cls, "Py_TPFLAGS_IMMUTABLETYPE"):
        utils.clear_on_alloc(cls)

        if original_attrs is None:
            for name, value in tuple(cls.__dict__.items()):
                if isinstance(value, utils._WrappedBase):
                    _unwrap_patched_attr(cls, name, value)
        else:
            for name, original in reversed(list(original_attrs.items())):
                _restore_attr(cls, name, original)

        if original_init_subclass is _MISSING:
            init_subclass = cls.__dict__.get("__init_subclass__")
            if _is_patch_generated_init_subclass(init_subclass):
                delattr(cls, "__init_subclass__")
        else:
            _restore_attr(cls, "__init_subclass__", original_init_subclass)

        if original_retrace_system is _MISSING:
            if "__retrace_system__" in cls.__dict__:
                delattr(cls, "__retrace_system__")
        else:
            _restore_attr(cls, "__retrace_system__", original_retrace_system)

        if original_retrace is _MISSING:
            if "__retrace__" in cls.__dict__:
                delattr(cls, "__retrace__")
        else:
            _restore_attr(cls, "__retrace__", original_retrace)

def patch_type(system, cls, install_session=None):
    """Patch *cls* in-place so its methods route through the gates.

        This is the central operation of the system.  After calling
        ``patch_type(cls)``:

        1. **External methods** — every callable and descriptor on
           *cls* (collected via ``superdict`` which walks the MRO) is
           replaced with a wrapper that routes through ``_external``.
           These are int→ext calls: code inside the sandbox calling a
           method on an outside-world type.

        2. **Allocation hook** — ``set_on_alloc`` installs ``_on_alloc``
           on the type's ``tp_alloc`` slot.  Whenever a new instance of
           *cls* (or a subclass) is created, the appropriate bind gate
           is notified.

        3. **Subclass patching** — if *cls* is extendable (can have
           Python subclasses), all *existing* subclasses are found via
           ``get_all_subtypes`` and patched as internal.  A custom
           ``__init_subclass__`` is installed on *cls* so that *future*
           subclasses are also patched automatically.

           Only subclass methods that **override** a name from the
           base type's MRO are wrapped.  C extension code can only
           dispatch to methods it knows about, so a brand-new method
           on the subclass can never be an ext→int callback target.
           Skipping non-overrides avoids unnecessary wrapping overhead.

           The wrapped overrides route through ``_internal`` (the
           ext→int gate).  This is how callbacks work: when C code in
           a base class method calls ``self.method()`` and the Python
           subclass overrides that method, the call goes through the
           internal gate where it can be recorded and where the
           external gate can be restored for any outbound calls the
           override makes (e.g. ``super().method()``).

        4. **Bind notification** — ``_bind(cls)`` is called to notify
           the current bind executor (if any) that a new type has
           entered the system.

        Parameters
        ----------
        cls : type
            The type to patch.  Must not be a BaseException subclass
            (exceptions are not proxied).  Must not already be in
            ``patched_types``.

        Returns
        -------
        cls : type
            The same type, now patched in-place.

        Example
        -------
            import _socket
            system = System()
            system.immutable_types.update({int, str, bytes, bool})
            patch_type(system, _socket.socket)
            # Now socket.connect(), socket.recv(), etc. all go through
            # the external gate when an executor is set.
            # A Python subclass that overrides recv() will have that
            # override routed through the internal gate.
        """

    assert isinstance(cls, type)
    assert not issubclass(cls, BaseException)

    existing = getattr(cls, "__retrace_system__", None)
    if existing is not None and existing is not system:
        raise RuntimeError(
            f"patch_type: {cls.__qualname__} is already patched by another System instance"
        )

    assert cls not in system.patched_types

    alloc_patch_undo = None
    patched_attrs = {}
    patched_subtypes = []
    subtype_alloc_undos = []
    subtype_attrs = {}
    original_init_subclass = cls.__dict__.get("__init_subclass__", _MISSING)
    original_retrace = cls.__dict__.get("__retrace__", _MISSING)
    original_retrace_system = cls.__dict__.get("__retrace_system__", _MISSING)
    bound_types = []

    def bind_patched_type(target):
        stream.Binder.add_bind_support(target)
        system.bind(target)
        bound_types.append(target)


    def proxy_attrs(target_cls, attr_dict, handler, originals):
        blacklist = system._patch_type_blacklist

        def proxy_function(func):
            return system._wrapped_function(handler=handler, target=func)

        def proxy_member(member):
            return system.descriptor_proxytype(type(member))(member)

        for name, value in attr_dict.items():
            if name in blacklist:
                continue

            if name not in originals:
                originals[name] = getattr(target_cls, name)

            def with_proxied(proxied):
                setattr(target_cls, name, proxied)
                if install_session is not None:
                    install_session.register_wrapped_attr(
                        owner=target_cls,
                        name=name,
                        target=value,
                        wrapped=proxied,
                    )

            if type(value) in [types.MemberDescriptorType, types.GetSetDescriptorType]:
                with_proxied(proxy_member(value))
            elif callable(value) and not isinstance(value, type):
                with_proxied(proxy_function(value))

    try:
        with WithoutFlags(cls, "Py_TPFLAGS_IMMUTABLETYPE"):
            alloc_patch_undo = utils.set_on_alloc(cls, system._on_alloc)
            system.patched_types.add(cls)

            base_methods = superdict(cls)
            proxy_attrs(
                cls,
                attr_dict=base_methods,
                handler=system.ext_gateway,
                originals=patched_attrs,
            )

            cls.__retrace_system__ = system

            if utils.is_extendable(cls):
                base_method_names = frozenset(base_methods.keys())

                # Bind the base before retro-patching existing subclasses so
                # replay sees the same binding order as record-time patching
                # followed by later subclass definition.
                bind_patched_type(cls)

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
                        handler=system.int_gateway,
                        originals=originals,
                    )

                cls.__init_subclass__ = classmethod(init_subclass)

                for subtype in get_all_subtypes(cls):
                    with WithoutFlags(subtype, "Py_TPFLAGS_IMMUTABLETYPE"):
                        init_subclass(subtype, patch_alloc=False)

            cls.__retrace__ = system

        if cls not in bound_types:
            bind_patched_type(cls)
    except Exception:
        for undo in reversed(subtype_alloc_undos):
            undo()

        if alloc_patch_undo is not None:
            alloc_patch_undo()

        for subtype in reversed(patched_subtypes):
            _unpatch_type_one(
                subtype,
                original_attrs=subtype_attrs.get(subtype, {}),
            )
            system.patched_types.discard(subtype)

        _unpatch_type_one(
            cls,
            original_attrs=patched_attrs,
            original_init_subclass=original_init_subclass,
            original_retrace_system=original_retrace_system,
            original_retrace=original_retrace,
        )

        for bound_type in reversed(bound_types):
            stream.Binder.remove_bind_support(bound_type)
            system.is_bound.discard(bound_type)

        system.patched_types.discard(cls)
        raise

    return cls
