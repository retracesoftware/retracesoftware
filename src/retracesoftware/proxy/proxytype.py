import retracesoftware.utils as utils
import retracesoftware.functional as functional

from retracesoftware.proxy.stubfactory import Stub

class DynamicProxy:
    __slots__ = []

def superdict(cls):
    result = {}
    for cls in list(reversed(cls.__mro__))[1:]:
        result.update(cls.__dict__)
    
    return result

def method_names(cls):
    for name, value in superdict(cls).items():
        if utils.is_method_descriptor(value):
            yield name

def dynamic_proxytype(handler, cls, wrapped_base = utils.ExternalWrapped):

    if cls.__module__.startswith('retracesoftware'):
        print(cls)
        utils.sigtrap('HERE5')
        
    assert not cls.__module__.startswith('retracesoftware')

    # print(f'In dynamic_proxytype: {cls}')

    assert not issubclass(cls, DynamicProxy)
    assert not issubclass(cls, BaseException)

    blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']

    spec = {}

    def wrap(func): return utils.wrapped_function(handler = handler, target = func)
    
    source_type = getattr(cls, "__retrace_target_type__", None) or cls
    if not hasattr(source_type, "__mro__"):
        source_type = cls

    for name in superdict(source_type).keys():
        if name not in blacklist:
            try:
                value = getattr(source_type, name)
            except AttributeError:
                # Some metatype attributes listed in the MRO dicts are not
                # readable on the concrete class (for example `type` exposes
                # `__abstractmethods__` here on 3.12). Skip those slots.
                continue

            if issubclass(type(value), utils.dispatch):
                value = utils.dispatch.table(value)['disabled']
            
            if utils.is_method_descriptor(value):
                spec[name] = wrap(value) 

    spec['__getattr__'] = wrap(getattr)
    spec['__setattr__'] = wrap(setattr)
    
    if utils.yields_callable_instances(cls):
        spec['__call__'] = handler

    spec['__retrace_target_class__'] = cls

    # target_type = functional.sequence(utils.unwrap, functional.typeof)

    target_type = cls.__retrace_target_type__ if issubclass(cls, Stub) else cls
    # functional.repeatedly(resolved)

    # spec['__class__'] = property(target_type)
    spec['__class__'] = property(functional.constantly(target_type))

    spec['__name__'] = cls.__name__
    spec['__module__'] = cls.__module__
    # name = f'retrace.proxied.{cls.__module__}.{cls.__name__}'

    return type(cls.__name__, (wrapped_base, DynamicProxy), spec)

def dynamic_int_proxytype(handler, cls, bind, checkpoint = None):
    proxytype = dynamic_proxytype(handler = handler, cls = cls, wrapped_base = utils.InternalWrapped)
    proxytype.__retrace_source__ = 'internal'

    bound = []

    for name, proxy in proxytype.__dict__.items():
        if isinstance(proxy, utils.wrapped_function):
            bind(proxy)
            bound.append(name)

    if checkpoint is not None:
        checkpoint({'int_proxytype': str(cls), 'bound': bound})

    return proxytype
