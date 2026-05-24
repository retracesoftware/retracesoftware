import enum
import inspect
import types
from retracesoftware.proxy.typeutils import modify
from retracesoftware.install.replace import restore_module_refs, update, update_module_refs
import retracesoftware.utils as utils
import retracesoftware.functional as functional
import importlib
from retracesoftware.install.installation import Installation

_MISSING_ATTR = object()


# ── Lightweight patcher for System ────────────────────────────────
#
# patch(module, spec, installation) applies a TOML-derived patch spec to a
# module using System (proxy/system.py).  Each TOML
# directive maps to a System method — no closures, no thread_state.
#
# Supported directives:
#
#   proxy          types → installation.patch_type / system.proxy_type
#                  functions → system.patch_function
#   ext_proxy_result
#                  functions → live-run and ext-proxy the returned object
#   patch_types    installation.patch_type (types only)
#   immutable      ImmutableRegistry.add_immutable_type
#   bind           pre-register objects (enums expanded to members)
#   disable        system.disable_for, replace in namespace
#   wrap           resolve dotted path, replace in namespace
#   patch_class    apply {attr: dotted_path} transforms to a class
#   type_attributes  recurse — apply directives to a type's attributes
#   stub_for_replay
#                  replay-only native-type stubs before normal proxying


class ReplayStubCallError(RuntimeError):
    """Raised if replay reaches an inert stub member directly."""


class _ReplayStubType(type):
    def __call__(cls, *args, **kwargs):
        return cls.__new__(cls, *args, **kwargs)


class _ReplayStubDescriptor:
    __retrace_replay_stub_descriptor__ = True

    def __init__(self, type_name, name):
        self._type_name = type_name
        self._name = name
        self.__name__ = name

    def _raise(self):
        raise ReplayStubCallError(
            f"replay stub member {self._type_name}.{self._name} executed "
            "directly; expected proxy boundary routing"
        )

    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        self._raise()

    def __set__(self, instance, value):
        self._raise()

    def __delete__(self, instance):
        self._raise()


def _make_replay_stub_method(type_name, qualname, module, name):
    def method(self, *args, **kwargs):
        raise ReplayStubCallError(
            f"replay stub method {type_name}.{name} executed directly; "
            "expected proxy boundary routing"
        )

    method.__module__ = module
    method.__name__ = name
    method.__qualname__ = f"{qualname}.{name}"
    return method


def _make_replay_stub_new(type_name, qualname, module):
    def __new__(cls, *args, **kwargs):
        return object.__new__(cls)

    __new__.__module__ = module
    __new__.__qualname__ = f"{qualname}.__new__"
    return __new__


def _replay_stub_source_attrs(cls):
    attrs = {}
    for base in list(reversed(cls.__mro__))[1:]:
        attrs.update(base.__dict__)
    return attrs


def replay_stub_type(cls):
    """Create an inert replay-time type with the original type's shape."""
    if not isinstance(cls, type):
        raise TypeError(f"expected type, got {type(cls).__name__!r}")

    module = getattr(cls, "__module__", "__main__")
    name = getattr(cls, "__name__", cls.__qualname__.rsplit(".", 1)[-1])
    qualname = getattr(cls, "__qualname__", name)
    type_name = f"{module}.{qualname}"
    namespace = {
        "__module__": module,
        "__qualname__": qualname,
        "__doc__": getattr(cls, "__doc__", None),
        "__new__": _make_replay_stub_new(type_name, qualname, module),
        "__retrace_target_type__": cls,
        "__retrace_stub_for_replay__": True,
    }

    skip_names = {
        "__class__",
        "__dict__",
        "__doc__",
        "__getattribute__",
        "__init__",
        "__module__",
        "__new__",
        "__slots__",
        "__weakref__",
    }
    descriptor_types = (
        property,
        types.GetSetDescriptorType,
        types.MemberDescriptorType,
    )

    for attr_name, value in _replay_stub_source_attrs(cls).items():
        if attr_name in skip_names:
            continue
        if isinstance(value, descriptor_types):
            namespace[attr_name] = _ReplayStubDescriptor(type_name, attr_name)
        elif callable(value) and not isinstance(value, type):
            namespace[attr_name] = _make_replay_stub_method(
                type_name,
                qualname,
                module,
                attr_name,
            )

    stub = _ReplayStubType(name, (object,), namespace)
    stub.__name__ = name
    return stub

def _is_function(obj):
    """True for built-in functions, Python functions, and method descriptors."""
    return (isinstance(obj, (types.BuiltinFunctionType,
                             types.FunctionType,
                             types.BuiltinMethodType,
                             types.WrapperDescriptorType,
                             types.MethodWrapperType,
                             types.MethodDescriptorType,
                             types.ClassMethodDescriptorType))
            or callable(obj) and not isinstance(obj, type))

def param_predicate(signature, param_name, predicate, *, fallback_index=None):
    if signature is None:
        if fallback_index is None:
            raise ValueError(f"Missing parameter '{param_name}'")
        idx = fallback_index
    else:
        idx = list(signature.parameters.keys()).index(str(param_name))
    extractor = functional.param(str(param_name), idx)
    return functional.sequence(extractor, predicate) 

def patch(
    module,
    spec,
    installation,
    update_refs=None,
    pathpredicate=None,
    module_ref_index=None,
):
    """Apply a TOML-derived patch spec to *module* using *installation*.

    Parameters
    ----------
    module : module or dict
        The module to patch.  If a module object, its ``__dict__`` is
        used as the namespace.  A raw dict (e.g. ``module.__dict__``)
        is also accepted.
    spec : dict
        Resolved TOML config for this module.  Keys are directive names
        (``proxy``, ``immutable``, ``disable``, etc.), values are lists
        of names or nested dicts.
    installation : Installation
        The installation that owns the system and install session for this
        patch application.
    update_refs : bool or None
        If True, globally replace old references with new values via
        ``gc.get_referrers`` (needed for already-imported modules).
    pathpredicate : callable or None
        If provided, a callable that takes a filesystem path (str)
        and returns True if the call should be retraced (proxied),
        False if it should passthrough to the original function.
        Used by the ``pathparam`` directive.
    module_ref_index : ModuleRefIndex or None
        Optional install-scoped module-reference index used to avoid scanning
        every loaded module for every already-loaded symbol replacement.

    Returns
    -------
    callable
        An undo function that restores the namespace to its pre-patch
        state.  Generated proxy types are bound back into the module namespace
        by the installation, which also owns restoration on uninstall.
    """
    if not isinstance(installation, Installation):
        raise TypeError(
            f"expected Installation, got {type(installation).__name__!r}"
        )

    system = installation.system
    if update_refs is None:
        update_refs = installation.update_refs

    namespace = module.__dict__ if hasattr(module, '__dict__') and not isinstance(module, dict) else module

    # Record every mutation so we can undo them.
    ns_undos = []       # (name, old_value, new_value, ref_update_mode, module_ref_changes)
    type_attr_undos = []  # (cls, attr, old_value)
    originals = {}      # name → first (pre-patch) value
    added_immutables = []  # legacy undo for old System immutable storage
    # Resolve dotted helper imports before mutating the module being patched.
    # This avoids importing support code (for example edgecase wrappers for
    # ``_io``) after core types in that same module have already been live-
    # patched.
    resolved_wrap = {
        name: resolve(dotted_path)
        for name, dotted_path in spec.get("wrap", {}).items()
    }
    resolved_patch_class = {
        name: {
            attr: resolve(transform)
            for attr, transform in transforms.items()
        }
        for name, transforms in spec.get("patch_class", {}).items()
    }

    def _apply(name, old, new):
        """Replace *name* in the namespace and optionally update refs."""
        if old is not new:
            namespace[name] = new
            installation.record_type_replacement(old, new)
            module_ref_changes = []
            if update_refs:
                module_ref_changes = (
                    module_ref_index.replace(old, new)
                    if module_ref_index is not None
                    else update_module_refs(old, new)
                )
            ns_undos.append((name, old, new, "all" if update_refs else None, module_ref_changes))
            originals.setdefault(name, old)
            if update_refs:
                update(old, new)

    def _wrap(wrapper_factory, value):
        try:
            parameters = inspect.signature(wrapper_factory).parameters
        except (TypeError, ValueError):
            parameters = {}
        if "system" in parameters:
            return wrapper_factory(value, system=system)
        return wrapper_factory(value)

    if getattr(system, "retrace_mode", None) == "replay":
        for name in spec.get("stub_for_replay", ()):
            if name not in namespace:
                continue
            value = namespace[name]
            if isinstance(value, type):
                _apply(name, value, replay_stub_type(value))

    for directive, config in spec.items():
        if directive == 'proxy':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                if not (isinstance(value, type) or callable(value)):
                    continue
                try:
                    patched = installation.patch_value(value)
                except Exception as exc:
                    module_name = namespace.get('__name__', '<unknown>')
                    raise RuntimeError(
                        f"failed to patch {module_name}.{name}"
                    ) from exc
                _apply(name, value, patched)

        elif directive == 'ext_proxy_result':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                if callable(value) and not isinstance(value, type):
                    _apply(name, value, system.ext_proxy_result(value))

        elif directive == 'patch_types':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                if isinstance(value, type):
                    patched = installation.patch_value(value)
                    _apply(name, value, patched)

        elif directive == 'immutable':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                if isinstance(value, type):
                    installation.add_immutable_type(value)
                    added_immutables.append(value)

        elif directive == 'bind':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                if isinstance(value, type) and issubclass(value, enum.Enum):
                    for member in value:
                        installation.bind(member)
                else:
                    installation.bind(value)

        elif directive == 'disable':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                new = system.disable_for(value, retrace=False)
                _apply(name, value, new)

        elif directive == 'wrap':
            for name, dotted_path in config.items():
                if name not in namespace:
                    continue
                value = namespace[name]
                wrapper_factory = resolved_wrap[name]
                new = _wrap(wrapper_factory, value)
                _apply(name, value, new)

        elif directive == 'patch_class':
            for name, transforms in config.items():
                if name not in namespace:
                    continue
                cls = namespace[name]
                if not isinstance(cls, type):
                    cls = originals.get(name, cls)
                if not isinstance(cls, type):
                    continue
                patch_class(resolved_patch_class.get(name, {}), cls)

        elif directive == 'type_attributes':
            for name, sub_spec in config.items():
                if name not in namespace:
                    continue
                cls = namespace[name]
                with modify(cls):
                        cls_ns = {}
                        def set_type_attr(attr, new_value):
                            old_value = cls.__dict__.get(attr, _MISSING_ATTR)
                            setattr(cls, attr, new_value)
                            type_attr_undos.append((cls, attr, old_value))

                        for sub_directive, sub_names in sub_spec.items():
                            if sub_directive == 'proxy':
                                for attr in sub_names:
                                    if not hasattr(cls, attr):
                                        continue
                                    value = getattr(cls, attr)
                                    if not callable(value) or isinstance(value, type):
                                        continue
                                    set_type_attr(attr, installation.patch_value(value))
                                continue

                            if sub_directive == 'disable':
                                for attr in sub_names:
                                    if not hasattr(cls, attr):
                                        continue
                                    value = getattr(cls, attr)
                                    if not callable(value) or isinstance(value, type):
                                        continue
                                    set_type_attr(
                                        attr,
                                        system.disabled_method_for(
                                            value,
                                            retrace=False,
                                        ),
                                    )
                                continue

                            if sub_directive == 'wrap':
                                for attr, dotted_path in sub_names.items():
                                    if not hasattr(cls, attr):
                                        continue
                                    value = getattr(cls, attr)
                                    wrapper_factory = resolve(dotted_path)
                                    set_type_attr(attr, _wrap(wrapper_factory, value))
                                continue

                            # Recurse: sub_spec is e.g. {"proxy": ["now", "utcnow"]}
                            # but targets are attributes on the class, not the module
                            cls_ns = {attr: getattr(cls, attr)
                                      for attr in (sub_names if isinstance(sub_names, list) else sub_names.keys())
                                      if hasattr(cls, attr)}
                            patch(
                                cls_ns,
                                {sub_directive: sub_names},
                                installation,
                                update_refs=update_refs,
                                pathpredicate=pathpredicate,
                                module_ref_index=module_ref_index,
                            )
                        for attr, new_val in cls_ns.items():
                            old_val = getattr(cls, attr, None)
                            if old_val is not new_val:
                                setattr(cls, attr, new_val)

        elif directive == 'pathparam':
            if pathpredicate is not None:
                for name, param_name in config.items():
                    if name in namespace:
                        patched = namespace[name]
                        try:
                            signature = inspect.signature(patched)
                        except (TypeError, ValueError):
                            signature = None

                        fallback_index = None
                        if (
                            signature is None
                            and namespace.get("__name__") == "posix"
                            and param_name in {"path", "src"}
                        ):
                            fallback_index = 0
                        elif signature is None:
                            continue

                        should_retrace = param_predicate(
                            signature = signature,
                            param_name = param_name,
                            predicate=pathpredicate,
                            fallback_index=fallback_index)

                        wrapped = functional.if_then_else(
                            should_retrace,
                            patched,
                            system.disable_for(patched, retrace=False),
                        )
                        namespace[name] = wrapped
                        module_ref_changes = []
                        if update_refs:
                            module_ref_changes = (
                                module_ref_index.replace(patched, wrapped)
                                if module_ref_index is not None
                                else update_module_refs(patched, wrapped)
                            )
                        ns_undos.append((name, patched, wrapped, "module", module_ref_changes))
                        originals.setdefault(name, patched)

        elif directive in ('default', 'ignore', 'stub_for_replay'):
            pass  # Informational directives, no action needed

    def undo():
        """Reverse all namespace mutations made by this patch call."""
        # Restore namespace entries in reverse order.
        for name, old_value, new_value, ref_update_mode, module_ref_changes in reversed(ns_undos):
            namespace[name] = old_value
            if ref_update_mode == "module":
                restore_module_refs(module_ref_changes)
            elif ref_update_mode == "all":
                restore_module_refs(module_ref_changes)
                update(new_value, old_value)
        # Remove types we added to the immutable set.
        for cls in added_immutables:
            immutable_types = getattr(system, "immutable_types", None)
            if immutable_types is not None:
                immutable_types.discard(cls)
        refresh_type_predicates = getattr(system, "_refresh_type_predicates", None)
        if refresh_type_predicates is not None:
            refresh_type_predicates()
        for cls, attr, old_value in reversed(type_attr_undos):
            with modify(cls):
                if old_value is _MISSING_ATTR:
                    delattr(cls, attr)
                else:
                    setattr(cls, attr, old_value)

    return undo

def resolve(path):
    module, sep, name = path.rpartition('.')

    if module is None:
        module = 'builtins'
    
    return getattr(importlib.import_module(module), name)

def replace(replacements, coll):
    return map(lambda x: replacements.get(x, x), coll)
        
def patch_class(transforms, cls):
    with modify(cls):
        for attr, wrapper in transforms.items():
            # Some C-extension APIs vary by Python build/version.
            # Skip missing attributes so module configs can remain portable.
            if hasattr(cls, attr):
                utils.update(cls, attr, wrapper)

    return cls
