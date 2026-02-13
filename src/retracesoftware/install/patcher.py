import enum
import types
from retracesoftware.proxy.typeutils import modify
from retracesoftware.install.replace import update
import threading
import sys
import retracesoftware.utils as utils
import retracesoftware.functional as functional
import importlib


# ── Hash patching ──────────────────────────────────────────────────

def install_hash_patching(system):
    """Patch ``__hash__`` on ``object`` and ``FunctionType`` for deterministic ordering.

    Uses ``system.create_gate`` to build a hash function that
    dispatches based on the system's primary gate state:

    - **Disabled** (no context active): returns ``None`` → identity hash.
    - **External / internal** (record/replay active): returns the next
      value from a deterministic ``utils.counter()`` → sequential hash.

    The gate piggybacks on the system's existing ``_external`` and
    ``_internal`` gates, so no manual ``set``/``disable`` is needed —
    it activates automatically when ``record_context`` or
    ``replay_context`` is entered.

    Call once during bootstrap, before any modules are loaded.
    """
    hashfunc = system.create_gate(
        disabled = functional.constantly(None),
        external = utils.counter(),
        internal = utils.counter())
    utils.patch_hashes(hashfunc, object, types.FunctionType)


# ── Lightweight patcher for System ─────────────────────────────────
#
# patch(module, spec, system) applies a TOML-derived patch spec to a
# module using the new System class (proxy/system.py).  Each TOML
# directive maps to a System method — no closures, no thread_state.
#
# Supported directives:
#
#   proxy          types → system.patch_type
#                  functions → route through the external gate
#   patch_types    system.patch_type (types only)
#   immutable      system.immutable_types.add
#   bind           pre-register objects (enums expanded to members)
#   disable        system.disable_for, replace in namespace
#   wrap           resolve dotted path, replace in namespace
#   patch_class    apply {attr: dotted_path} transforms to a class
#   type_attributes  recurse — apply directives to a type's attributes
#   patch_hash     handled by install_hash_patching (above)

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


def patch(module, spec, system, update_refs = False):
    """Apply a TOML-derived patch spec to *module* using *system*.

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
    system : System
        The proxy system that owns the gates and patching machinery.
    update_refs : bool
        If True, globally replace old references with new values via
        ``gc.get_referrers`` (needed for already-imported modules).
    """
    namespace = module.__dict__ if hasattr(module, '__dict__') and not isinstance(module, dict) else module

    def _apply(name, old, new):
        """Replace *name* in the namespace and optionally update refs."""
        if old is not new:
            namespace[name] = new
            if update_refs:
                update(old, new)

    for directive, config in spec.items():

        if directive == 'proxy':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                patched = system.patch(value)
                _apply(name, value, patched)

        elif directive == 'patch_types':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                if isinstance(value, type):
                    system.patch_type(value)

        elif directive == 'immutable':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                if isinstance(value, type):
                    system.immutable_types.add(value)

        elif directive == 'bind':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                if isinstance(value, type) and issubclass(value, enum.Enum):
                    for member in value:
                        system._bind(member)
                else:
                    system._bind(value)

        elif directive == 'disable':
            for name in config:
                if name not in namespace:
                    continue
                value = namespace[name]
                new = system.disable_for(value)
                _apply(name, value, new)

        elif directive == 'wrap':
            for name, dotted_path in config.items():
                if name not in namespace:
                    continue
                value = namespace[name]
                new = resolve(dotted_path)
                _apply(name, value, new)

        elif directive == 'patch_class':
            for name, transforms in config.items():
                if name not in namespace:
                    continue
                cls = namespace[name]
                patch_class(transforms, cls)

        elif directive == 'type_attributes':
            for name, sub_spec in config.items():
                if name not in namespace:
                    continue
                cls = namespace[name]
                with modify(cls):
                    for sub_directive, sub_names in sub_spec.items():
                        # Recurse: sub_spec is e.g. {"proxy": ["now", "utcnow"]}
                        # but targets are attributes on the class, not the module
                        cls_ns = {attr: getattr(cls, attr)
                                  for attr in (sub_names if isinstance(sub_names, list) else sub_names.keys())
                                  if hasattr(cls, attr)}
                        patch(cls_ns, {sub_directive: sub_names}, system)
                        for attr, new_val in cls_ns.items():
                            old_val = getattr(cls, attr, None)
                            if old_val is not new_val:
                                utils.update(cls, attr, new_val)

        elif directive == 'patch_hash':
            pass  # Deferred — requires deterministic hash counter

        elif directive in ('default', 'ignore'):
            pass  # Informational directives, no action needed

def resolve(path):
    module, sep, name = path.rpartition('.')

    if module is None:
        module = 'builtins'
    
    return getattr(importlib.import_module(module), name)

def replace(replacements, coll):
    return map(lambda x: replacements.get(x, x), coll)
        
def patch_class(transforms, cls):
    with modify(cls):
        for attr,transform in transforms.items():
            utils.update(cls, attr, resolve(transform))

    return cls

class PerThread(threading.local):
    def __init__(self):
        self.internal = utils.counter()
        self.external = utils.counter()

def create_patcher(system):

    patcher = {}

    def foreach(func): return lambda config: {name: func for name in config}
    def selector(func): return lambda config: {name: functional.partial(func, value) for name, value in config.items()}

    def simple_patcher(func): return foreach(functional.side_effect(func))

    def type_attributes(transforms, cls):
        with modify(cls):
            for action, attrs in transforms.items():
                for attr,func in patcher[action](attrs).items():
                    utils.update(cls, attr, func)
        return cls

    def bind(obj):
        if issubclass(obj, enum.Enum):
            for member in obj:
                system.bind(member)
        else:
            system.bind(obj)

    def add_immutable_type(obj):
        if not isinstance(obj, type):
            raise Exception("TODO")
        system.immutable_types.add(obj)
        return obj

    per_thread = PerThread()
        
    # Hash patching for deterministic record/replay
    # 
    # Why we patch __hash__:
    # Python's default object hashes are based on memory addresses, which vary
    # between runs. Sets iterate in hash order, so iteration order is 
    # non-deterministic. For record/replay to work correctly, we need identical
    # set iteration order during both phases, so we replace __hash__ with a
    # deterministic counter-based hash that returns stable values.
    # (Note: dicts maintain insertion order since Python 3.7, so they're stable.)
    #
    # The hashfunc dispatches based on thread state:
    # - disabled: returns None (unhashable, triggers fallback)
    # - internal: counter for when thread is inside the sandbox (proxied code)
    # - external: counter for when thread is outside the sandbox (user code)
    #
    # IMPORTANT - Python 3.12 compatibility:
    # Python 3.12 changed typing._tp_cache to use a global _caches dict with
    # functions as keys (see bpo GH-98253). If typing is imported BEFORE we
    # patch FunctionType.__hash__, those functions won't be in our internal
    # hash cache, and lookups will fail. utils.patch_hash handles this by
    # pre-populating the cache with all existing instances of the patched type.
    #
    hashfunc = system.thread_state.dispatch(
        functional.constantly(None),
        internal = functional.repeatedly(functional.partial(getattr, per_thread, 'internal')),
        external = functional.repeatedly(functional.partial(getattr, per_thread, 'external')))

    def patch_hash(obj):
        """Patch __hash__ on a type to use deterministic counter-based hashing.
        
        This ensures stable set iteration order for instances of this type,
        which is required for record/replay consistency.
        """
        if not isinstance(obj, type):
            raise Exception("TODO")

        utils.patch_hash(cls = obj, hashfunc = hashfunc)
        return obj
    
    patcher.update({
        'type_attributes': selector(type_attributes),
        'patch_class': selector(patch_class),
        'disable': foreach(system.disable_for),
        'patch_types': simple_patcher(system.patch_type),
        'proxy': foreach(system),
        'bind': simple_patcher(bind),
        'wrap': lambda config: {name: resolve(action) for name,action in config.items() },
        'immutable': simple_patcher(add_immutable_type),
        'patch_hash': simple_patcher(patch_hash),
    })
    return patcher

def patch_namespace(patcher, config, namespace, update_refs):
    for phase_name, phase_config in config.items():
        if phase_name in patcher:
            for name,func in patcher[phase_name](phase_config).items():
                if name in namespace:
                    # print(f"patching: {name}")

                    value = namespace[name]

                    try:
                        new_value = func(value)
                    except Exception as e:
                        print(f"Error patching {name}, phase: {phase_name}: {e}")
                        raise e
                    
                    if value is not new_value:
                        namespace[name] = new_value

                        if update_refs:
                            update(value, new_value)
        else:
            print(phase_name)
            utils.sigtrap('FOO1')
    
def patch_module(patcher, config, namespace, update_refs):
    if '__name__' in namespace:
        name = namespace['__name__']
        if name in config:
            patch_namespace(patcher, config = config[name], namespace=namespace, update_refs=update_refs)

def patch_imported_module(patcher, checkpoint, config, namespace, update_refs):
    if '__name__' in namespace:
        name = namespace['__name__']
        checkpoint(f"importing module: {name}")

        if name in config:
            # print(f"Patching imported module: {name}")
            # checkpoint(f"Patching imported module: {name}")
            patch_namespace(patcher, config = config[name], namespace=namespace, update_refs=update_refs)
