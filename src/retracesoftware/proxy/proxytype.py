import retracesoftware.utils as utils
import retracesoftware.functional as functional

import types

from retracesoftware.proxy.stubfactory import StubMethodDescriptor, Stub

class Proxy:
    __slots__ = []

class DynamicProxy(Proxy):
    __slots__ = []

class ExtendingProxy(Proxy):
    __slots__ = []

class InternalProxy:
    __slots__ = []

@functional.memoize_one_arg
def unproxy_type(cls):
    if not issubclass(cls, Proxy):
        return cls

    # Prefer attribute on the class, not on the mappingproxy
    target = getattr(cls, '__retrace_target_class__', None)
    if target is not None:
        return unproxy_type(target)

    # Rebuild with unproxied bases, preserving metaclass and critical dunder attrs
    new_bases = tuple(map(unproxy_type, cls.__bases__))

    # Copy attrs, but omit implementation-managed ones
    attrs = {}
    classcell = None
    for k, v in cls.__dict__.items():
        if k in ('__dict__', '__weakref__'):
            continue
        if k == '__classcell__':
            # must be passed through at class creation for zero-arg super()
            classcell = v
            continue
        attrs[k] = v

    # Ensure module/doc are kept (in case they weren't in __dict__)
    attrs.setdefault('__module__', cls.__module__)
    attrs.setdefault('__doc__', cls.__doc__)

    # Use types.new_class if you want full class-creation protocol
    def exec_body(ns):
        ns.update(attrs)
        if classcell is not None:
            ns['__classcell__'] = classcell

    return types.new_class(cls.__name__, new_bases, {'metaclass': type(cls)}, exec_body)


def superdict(cls):
    result = {}
    for cls in list(reversed(cls.__mro__))[1:]:
        result.update(cls.__dict__)
    
    return result

def is_method_descriptor(obj):
    return isinstance(obj, types.FunctionType) or \
           (isinstance(obj, (types.WrapperDescriptorType, 
                             types.MethodDescriptorType,
                             StubMethodDescriptor)) and obj.__objclass__ != object)

def proxy_method_descriptors(cls, handler):
    for name, target in cls.__dict__.items():
        if is_method_descriptor(target):
            proxied = utils.wrapped_function(handler = handler, target = target)
            setattr(cls, name, proxied)

def methods(cls):
    for name,value in superdict(cls).items():
        if is_descriptor(value) and is_method_descriptor(value):
            yield name

def is_descriptor(obj):
    return hasattr(obj, '__get__') or hasattr(obj, '__set__') or hasattr(obj, '__delete__')

class Named:
    def __init__(self, name):
        self.__name__ = name

class DescriptorStub:

    __slots__ = ['handler', 'name']

    def __init__(self, handler, name):
        self.handler = handler
        self.name = name

    def __get__(self, instance, owner):
        return self.handler(Named('getattr'), self.name)

    def __set__(self, instance, value):
        return self.handler(Named('setattr'), self.name, value)

    def __delete__(self, instance):
        return self.handler(Named('delattr'), self.name)
    
def stubtype_from_spec(handler, module, name, methods, members):

    spec = {
        '__module__': module,        
    }

    for method in methods:
        spec[method] = utils.wrapped_function(
            handler = handler, target = Named(method))

    for member in members:
        spec[member] = DescriptorStub(handler = handler, name = member)

    return type(name, (Stub, DynamicProxy,), spec)

    # stubtype.__new__ = thread_state.dispatch(disabled__new__, internal = stub.__new__, external = stub.__new__)
    # stub.__retrace_unproxied__ = cls

def dynamic_stubtype(handler, cls):

    assert not issubclass(cls, BaseException)

    blacklist = ['__getattribute__', '__hash__', '__del__', '__call__']

    to_proxy = [m for m in methods(cls) if m not in blacklist]

    def wrap(name): return utils.wrapped_function(handler = handler, 
                                                  target = Named(name))
    
    spec = { name: wrap(name) for name in to_proxy }

    spec['__getattr__'] = wrap('__getattr__')
    spec['__setattr__'] = wrap('__setattr__')
    
    if utils.yields_callable_instances(cls):
        spec['__call__'] = handler

    spec['__retrace_target_class__'] = cls

    target_type = functional.sequence(utils.unwrap, functional.typeof)
    spec['__class__'] = property(target_type)

    spec['__module__'] = cls.__module__
    spec['__qualname__'] = cls.__name__
    
    # name = f'retrace.proxied.{cls.__module__}.{cls.__name__}'

    return type(cls.__name__, (Stub, DynamicProxy), spec)

class DescriptorProxy:
    __slots__ = ['target', 'handler']

    def __init__(self, handler, target):
        self.handler = handler
        self.target = target

    def __get__(self, obj, cls):
        try:
            return self.handler(self.target.__get__, obj, cls)
        except:
            print(f'error calling __get__')
            raise

    def __set__(self, obj, value):
        return self.handler(self.target.__set__, obj, value)
    
    def __delete__(self, obj):
        return self.handler(self.target.__delete__, obj)

# class ExtendingDescriptorProxy:

#     __slots__ = ['handler', 'proxytype', 'name']

#     def __init__(self, proxytype, handler, name):
#         self.proxytype = proxytype
#         self.handler = handler
#         self.name = name

#     def __get__(self, instance, owner):
#         inst = owner if instance is None else instance
#         getter = functional.partial(getattr, super(self.proxytype, inst))
#         return self.handler(getter, self.name)

#     def __set__(self, instance, value):
#         breakpoint()
#         setter = functional.partial(setattr, super(self.proxytype, instance))
#         return self.handler(setter, self.name, value)

#     def __delete__(self, instance):
#         deleter = functional.partial(delattr, super(self.proxytype, instance))
#         return self.handler(deleter, self.name)


def dynamic_proxytype(handler, cls):

    if cls.__module__.startswith('retracesoftware'):
        print(cls)
        utils.sigtrap('HERE5')
        
    assert not cls.__module__.startswith('retracesoftware')

    # print(f'In dynamic_proxytype: {cls}')

    assert not issubclass(cls, Proxy)
    assert not issubclass(cls, BaseException)

    blacklist = ['__getattribute__', '__hash__', '__del__', '__call__', '__new__']

    spec = {}

    def wrap(func): return utils.wrapped_function(handler = handler, target = func)
    
    for name in superdict(cls).keys():
        if name not in blacklist:
            value = getattr(cls, name)

            if issubclass(type(value), utils.dispatch):
                value = utils.dispatch.table(value)['disabled']
            
            # if is_descriptor(value):
            if utils.is_method_descriptor(value):
                spec[name] = wrap(value) 

            # try:
            #     value = getattr(cls, name)

            #     assert type(value) is not utils.dispatch

            #     if is_descriptor(value):
            #         if utils.is_method_descriptor(value):
            #             spec[name] = wrap(value)
            # except Exception as error:
            #     print(f'FOO! {cls} {name} {error}')
            #     breakpoint()
            #     raise
    
                # else:
                #     spec[name] = DescriptorProxy(handler = handler, target = value)

    # to_proxy = [m for m in methods(cls) if m not in blacklist]

    # def wrap(target): return utils.wrapped_function(handler = handler, target = target)
    
    # spec = { name: wrap(getattr(cls, name)) for name in to_proxy }

    # def foo(obj, name, default = None):        
    #     print(f'dynamic_proxytype.__getattr__: {type(obj).__mro__} {name}')
    #     utils.sigtrap(obj)
    #     return getattr(obj, name, default)

    # spec['__getattr__'] = wrap(foo)    
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

    return type(cls.__name__, (utils.Wrapped, DynamicProxy), spec)

def dynamic_from_extended(cls):
    
    base = cls.__base__

    name = f'retrace.proxied1.{base.__module__}.{base.__name__}'

    spec = dict(cls.__dict__)

    spec['__retrace_target_class__'] = base

    del spec['__init_subclass__']
    # del spec['__new__']

    target_type = functional.sequence(utils.unwrap, functional.typeof)
    spec['__class__'] = property(target_type)
    
    return type(name, (utils.Wrapped, DynamicProxy), spec)


def instantiable_dynamic_proxytype(handler, cls, thread_state, create_stub = False):

    proxytype = dynamic_proxytype(handler = handler, cls = cls)

    def create_original(proxytype, *args, **kwargs):
        instance = cls(*args, **kwargs)
        instance.__init__(*args, **kwargs)
        return instance
    
    def __new__(proxytype, *args, **kwargs):
        instance = utils.create_stub_object(cls) if create_stub else cls(*args, **kwargs)
        return utils.create_wrapped(proxytype, instance)

    proxytype.__new__ = thread_state.dispatch(create_original, internal = __new__)

    return proxytype    

def dynamic_int_proxytype(handler, cls, bind):
    proxytype = dynamic_proxytype(handler = handler, cls = cls)
    proxytype.__new__ = functional.sequence(proxytype.__new__, functional.side_effect(bind))
    proxytype.__retrace_source__ = 'internal'
    return proxytype
                                    

blacklist = ['__getattribute__', '__hash__', '__del__', '__dict__']

# if the type can be patched, thats better, all new instances must be of correct type

# def make_extensible(thread_state, proxy_method_descriptor, cls):

#     @functional.memoize_one_arg
#     def proxy_subclass(subclass):
#         if '__retrace_target_type__' in subclass.__dict__:
#             return subclass
        
#         attrs = {k: proxy_method_descriptor(v) for k, v in subclass.__dict__.items() if is_method_descriptor(v) }

#         # Ensure module/doc are kept (in case they weren't in __dict__)
#         attrs.setdefault('__module__', subclass.__module__)
#         attrs.setdefault('__doc__', subclass.__doc__)
#         attrs['__retrace_target_type__'] = subclass

#         return type(subclass.__name__, (subclass,), attrs)

#     orig__new__ = cls.__new__

#     def proxied__new__(subclass, *args, **kwargs):
#         nonlocal orig__new__
#         return orig__new__(proxy_subclass(subclass), *args, **kwargs)
                    
#     def unproxied__new__(subclass, *args, **kwargs):
#         return unproxy_type(subclass)(*args, **kwargs)
    
#     # Dispatch which constructor to use based on your thread_state policy
#     cls.__new__ = thread_state.dispatch(unproxied__new__, internal = proxied__new__)

def create_unproxied_type(cls):
    def unproxy_type(cls):
        return cls.__dict__.get('__retrace_unproxied__', cls)

    bases = tuple(map(unproxy_type, cls.__bases__))
    slots = dict(cls.__dict__)

    if '__slots__' in slots:
        for slot in slots['__slots__']:
            slots.pop(slot)

    # del slots['__init_subclass__']
    return type(cls.__name__, tuple(bases), slots)

def extending_proxytype(cls, base, thread_state, ext_handler, int_handler, on_subclass_new):

    # assert cls is base
    assert not issubclass(cls, BaseException)
    assert not issubclass(cls, ExtendingProxy)

    def init_subclass(subclass, **kwargs):
        # print(f'In init_subclass: {subclass} {subclass.__mro__} {kwargs}')
        unproxied = create_unproxied_type(subclass)
        subclass.__retrace_unproxied__ = unproxied

        proxy_method_descriptors(cls = subclass, handler = int_handler)

        if not issubclass(subclass, InternalProxy):
            subclass.__new__ = functional.sequence(subclass.__new__, functional.side_effect(on_subclass_new))
            subclass.__bases__ = subclass.__bases__ + (InternalProxy,)

        assert not issubclass(subclass.__retrace_unproxied__, ExtendingProxy)

    slots = { "__slots__": (), 
              "__retrace_unproxied__": cls,
              "__module__": cls.__module__,
              "__init_subclass__": init_subclass }

    def wrap(target): return utils.wrapped_function(handler = ext_handler, target = target)

    descriptors = []

    for name,value in superdict(cls).items():
        if name not in blacklist:
            if is_method_descriptor(value):
                slots[name] = wrap(getattr(base, name))
            elif is_descriptor(value):
                descriptors.append(name)

    slots['__retrace_unproxied__'] = cls

    extended = type(cls.__name__, (base, ExtendingProxy), slots)

    for name in descriptors:
        # proxy = ExtendingDescriptorProxy(handler = ext_handler, name = name, proxytype = extended)
        proxy = DescriptorProxy(handler = ext_handler, target = getattr(cls, name))
        setattr(extended, name, proxy)
                    
    def unproxied__new__(subclass, *args, **kwargs):
        return subclass.__retrace_unproxied__(*args, **kwargs)
      
    assert callable(base.__new__)

    extended.__new__ = thread_state.dispatch(unproxied__new__, internal = base.__new__)

    assert not issubclass(extended.__retrace_unproxied__, ExtendingProxy)
    assert extended.__dict__['__retrace_unproxied__'] is extended.__retrace_unproxied__
    # print(f'FOO: {extended} {id(extended)} {id(extended.__retrace_unproxied__)}')
    # breakpoint()

    return extended
