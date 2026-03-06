from retracesoftware.utils import counter
from retracesoftware.functional import *

import types
import pickle
import gc
import os
import functools
import inspect

def is_function_type(cls):
    return issubclass(cls, types.BuiltinFunctionType) or issubclass(cls, types.FunctionType)

def superdict(cls):
    result = {}
    for cls in list(reversed(cls.__mro__))[1:]:
        result.update(cls.__dict__)
    
    return result

def is_method_descriptor(obj):
    return isinstance(obj, types.FunctionType) or \
           (isinstance(obj, (types.WrapperDescriptorType, types.MethodDescriptorType)) and obj.__objclass__ != object)

def blacklist_for(self, cls):
    return ['__getattribute__', '__hash__', '__del__', '__init__', '__call__']

def is_descriptor(obj):
    return hasattr(obj, '__get__') or hasattr(obj, '__set__') or hasattr(obj, '__delete__')

def methods(cls):
    for name,value in superdict(cls).items():
        if is_descriptor(value) and is_method_descriptor(value):
            yield name

def patch_spec(cls, blacklist):
    return {
        'name': f'retrace.proxied.{cls.__module__}.{cls.__name__}',
        'methods': [m for m in methods(cls) if m not in blacklist],
        'callable': yeilds_callable_instances(cls),
        'weakrefs': yeilds_weakly_referenceable_instances(cls)
    }

def proxytype_from_spec(target_for, spec):
    proxytype = create_wrapping_proxy_type(
        name = spec['name'],
        callable = spec['callable'],
        weakrefs = spec['weakrefs'])
    
    def add_method_descriptor(name, target):
        setattr(proxytype, name, proxytype.method_descriptor(name = name, target = target))

    for method in spec['methods']:
        add_method_descriptor(name = method, target = target_for(method))

    # add_method_descriptor(name = '__getattr__', target = object.__getattribute__)
    add_method_descriptor(name = '__getattr__', target = getattr)
    add_method_descriptor(name = '__setattr__', target = setattr)

    return proxytype

def stubtype_from_spec(spec):
    proxytype = create_stub_proxy_type(
        name = spec['name'],
        callable = spec['callable'],
        weakrefs = spec['weakrefs'])

    def add_method_descriptor(name):
        setattr(proxytype, name, proxytype.method_descriptor(name))

    for method in spec['methods']:
        add_method_descriptor(method)

    add_method_descriptor('__getattr__')
    add_method_descriptor('__setattr__')

    return proxytype
    
def wrap_method_descriptors(wrapper, prefix, base):
    slots = {"__slots__": () }

    extended = type(f'{prefix}.{base.__module__}.{base.__name__}', (base,), {"__slots__": () })

    blacklist = ['__getattribute__', '__hash__', '__del__']

    for name,value in superdict(base).items():
        if name not in blacklist:
            if is_method_descriptor(value):
                setattr(extended, name, wrapper(value))

    return extended

def sync_type(sync, base):
    return wrap_method_descriptors(
        wrapper = lambda desc: intercept(on_call = sync, function = desc),
        prefix = 'retrace.synced',
        base = base)

class GCHook:
    def __init__(self, thread_state):
        self.thread_state = thread_state

    def __call__(self, phase, info):
        if phase == 'start':
            self.saved_state = self.thread_state.value
            self.thread_state.value = 'disabled'

        elif phase == 'stop':
            self.thread_state.value = self.saved_state

# class ProxyFactory:

#     def ext_proxy_factory(self):
#         return wrapping_proxy_factory(proxytype = self.proxytype, create_reference = compose(type, Reference), handler = self.ext_handler)
    
#     def int_proxy_factory(self):
#         return wrapping_proxy_factory(proxytype = self.proxytype, create_reference = Reference, handler = self.int_handler)
    
#     def ext_call_handler(self):
#         return self.int_handler.call_handler
    
#     def int_call_handler(self):
#         return self.ext_handler.call_handler

#     def before_fork(self):
#         pass
#         # self.state_before_fork = self.thread_state.value
#         # self.thread_state.value = 'disabled'

#     def after_fork_in_child(self):
#         self.fork_counter = 0

#     def after_fork_in_parent(self):
#         self.fork_counter += 1
#         # self.thread_state.value = self.state_before_fork

#     def gc_start(self):
#         self.before_gc = self.thread_state.value
#         self.thread_state.value = 'external'

#     def gc_end(self):
#         self.thread_state.value = self.before_gc
#         del self.before_gc

#     def gc_hook(self, phase, info):

#         if phase == 'start':
#             self.gc_start()

#         elif phase == 'stop':
#             self.gc_end()
    
#     def __init__(self, thread_state, on_new_proxytype, sync, 
#                  debug, checkpoint, verbose,
#                  ext_proxy, ext_handler, int_proxy, int_handler):
        
#         assert ext_proxy
        
#         self.ext_proxy = ext_proxy
#         self.ext_handler = ext_handler
#         self.int_proxy = int_proxy
#         self.int_handler = int_handler

#         def normalize(obj):
#             if isinstance(obj, RootProxy):
#                 return str(obj)
#             elif isinstance(obj, MethodDescriptor):
#                 return f'{obj.__objclass__.__module__}.{obj.__objclass__.__name__}.{obj.__name__}'
#             elif isinstance(obj, Proxy):
#                 return 'Proxy'
#             else:
#                 return obj

#         self.normalize = walker(normalize)

#         self.checkpoint_ext_call = mapargs(transform = self.normalize, 
#                                            function = self.checkpoint_ext_call)
        
#         # self.checkpoint_ext_call = self.arg_serializer(self.checkpoint_ext_call)

#         self.thread_state = thread_state
#         self.debug = debug
#         self.fork_counter = 0
#         self.verbose = verbose

#         self.on_new_proxytype = on_new_proxytype
#         self._sync = sync
#         self.thread_counter = self.sync_function(counter(1))

#         # immutable_types = self.disable_for(immutable_types)

#         gc.callbacks.append(self.gc_hook)

#         def before():
#             print("In before!!!!")
#             with self.thread_state.select('disabled'):
#                 self.before_fork()

#         os.register_at_fork(
#             # before = self.thread_state.wrap('disabled', self.before_fork),
#             before = before,
#             after_in_parent = self.thread_state.wrap('disabled', self.after_fork_in_parent),
#             after_in_child = self.thread_state.wrap('disabled', self.after_fork_in_child))

#         self.tracing = None

#         if debug > 3:

#             # def checkpoint(obj):
#             #     print(f'in checkpoint: {obj}')
#             #     self.checkpoint(obj)

#             FrameTracer.install(self.thread_state.dispatch(_proxy.noop, internal = checkpoint))

#             # import threading
#             # tracer = FrameTracer(
#             #     pred = self.thread_state.predicate('internal'), 
#             #     checkpoint = checkpoint)
            
#             # sys.settrace(tracer)
#             # threading.settrace(tracer)            
#             # self.trace = lambda event, **kwargs: checkpoint({'type': 'trace', 'event': event} | kwargs)
#             # self.enable_tracing()            

#     def sync_type(self, base):
#         return sync_type(sync = self.sync, base = base)

#     def sync_function(self, function):
#         return intercept(on_call = self._sync, function = function)

#     def checkpoint(self, obj):
#         pass

#     def log(self, message):
#         if self.verbose:
#             print(message)

#         self.checkpoint({'type': 'log', 'message': message})

#     @property
#     def disable(self):
#         return self.thread_state.select('disabled')

#     def with_state(self, state, function):
#         return self.thread_state.wrap(desired_state = state, function = function)

#     def disable_for(self, function):
#         return self.thread_state.wrap(desired_state = 'disabled', function = function)

#     def __call__(self, module, name, obj):
        
#         if type(obj) == type:
#             try:
#                 return self.extend_type(obj)
#             except:
#                 pass
        
#         self.checkpoint({'type': 'proxy', 'module': module, 'name': name})

#         if is_function_type(type(obj)) or type(obj) == type:
#             proxied = RootProxy(module = module, name = name, handler = self.ext_handler, target = obj)
            
#             try:
#                 # print(f"signature: {inspect.signature(obj)} for {obj}")
#                 proxied.__signature__ = inspect.signature(obj)
#             except:
#                 pass

#             return proxied
#         else:
#             return self.ext_proxy(obj)
        
#         # return self.ext_proxy(obj)
    
#     def start_new_thread(self, start_new_thread, function, *args):
#         # synchronized, replay shoudl yeild correct number
#         thread_id = self.thread_counter()

#         def threadrunner(*args, **kwargs):
#             self.set_thread_number(thread_id)
#             with self.thread_state.select('internal'):
#                 if self.tracing:
#                     FrameTracer.install(self.thread_state.dispatch(noop, internal = self.checkpoint))
                
#                 return function(*args, **kwargs)

#         return start_new_thread(threadrunner, *args)

#     def wrap_start_new_thread(self, start_new_thread):
#         wrapped = functools.partial(self.start_new_thread, start_new_thread)
#         return self.thread_state.dispatch(start_new_thread, internal = wrapped)
    
#     def checkpoint_ext_call(self, func, *args, **kwargs):
#         self.checkpoint({'type': 
#                          'external.call', 
#                          'function': str(func)})

#     def extend_type(self, base):

#         assert not issubclass(base, BaseException)

#         self.checkpoint({'type': 'log', 'message': f'extending type: {base}'})

#         def custom_init_subclass(cls, **kwargs):
#             for name, target in cls.__dict__.items():
#                 if is_method_descriptor(target):
#                     proxied = cls.method_descriptor(handler = self.int_handler, 
#                                                     call_handler = self.thread_state.predicate('external'),
#                                                     name = name,
#                                                     target = target)
#                     setattr(cls, name, proxied)

#         def __new__(cls, *args, **kwargs):
#             instance = base.__new__(cls, *args, **kwargs)
#             self.on_new(instance)
#             return instance
        
#         slots = {
#             "__slots__": (),
#             '__new__': __new__,
#             "__init_subclass__": classmethod(custom_init_subclass)
#         }

#         extended = type(f'retrace.extended.{base.__module__}.{base.__name__}', (base, _proxy.ExtendedProxy), slots)

#         blacklist = ['__getattribute__', '__hash__', '__del__']

#         for name,value in superdict(base).items():
#             if name not in blacklist:
#                 if is_method_descriptor(value):
#                     proxied = extended.method_descriptor(
#                         handler = self.ext_handler,
#                         name = name,
#                         target = getattr(base, name))

#                     setattr(extended, name, proxied)
#                 elif is_descriptor(value):
#                     setattr(extended, name, self.ext_proxy(value))
                
#         def __del__(obj):
#             try:
#                 self.on_del(Reference(obj))
#                 # self.external.handler.on_del(_proxy.Reference(obj))
#             except:
#                 pass

#             try:
#                 base.__del__(obj)
#             except:
#                 pass

#         extended.__del__ = __del__

#         if self.on_new_proxytype:
#             self.on_new_proxytype(base.__module__, base.__name__, extended)

#         return extended
