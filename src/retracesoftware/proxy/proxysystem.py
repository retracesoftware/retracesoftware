import retracesoftware.functional as functional
import retracesoftware.utils as utils
import types
from retracesoftware.protocol.normalize import normalize as normalize_for_checkpoint
from retracesoftware.proxy.gateway import adapter_pair
from types import SimpleNamespace
from retracesoftware.proxy.proxytype import *
from retracesoftware.proxy.stubfactory import Stub
from retracesoftware.install.typeutils import modify, WithFlags, WithoutFlags

from retracesoftware.proxy.serializer import serializer
from retracesoftware.install.tracer import Tracer
import sys
import gc
import weakref
import enum
import functools
import re

from retracesoftware.exceptions import RetraceError

def proxy(proxytype):
    return functional.spread(
        utils.create_wrapped,
        functional.sequence(functional.typeof, proxytype),
        None)

def maybe_proxy(proxytype):
    return functional.if_then_else(
            functional.isinstanceof(utils.Wrapped),
            utils.unwrap,
            proxy(functional.memoize_one_arg(proxytype)))

class Patched:
    __slots__ = ()

class RetraceBase:
    pass

unproxy_execute = functional.mapargs(starting = 1, 
                                     transform = functional.walker(utils.try_unwrap), 
                                     function = functional.apply)

def resolve(obj):
    try:
        return getattr(sys.modules[obj.__module__], obj.__name__)
    except:
        return None

def is_function_type(cls):
    return cls in [types.BuiltinFunctionType, types.FunctionType]

method_types = (types.MethodDescriptorType,
                types.WrapperDescriptorType,
                types.FunctionType)

def is_instance_method(obj):
    return isinstance(obj, method_types)

def get_all_subtypes(cls):
    """Recursively find all subtypes of a given class."""
    subclasses = set(cls.__subclasses__())
    for subclass in cls.__subclasses__():
        subclasses.update(get_all_subtypes(subclass))
    return subclasses

def cleanse(text):
    pattern = r'0x[a-fA-F0-9]+'
    return re.sub(pattern, '0x####', text)

excludes = [
        "ABCMeta.__instancecheck__",
        "ABCMeta.__subclasscheck__",
        "Collection.__subclasshook__",
        re.compile(r"WeakSet"),
        ]

def exclude(text):
    for elem in excludes:
        if isinstance(elem, str):
            if elem == text: return True
        else:
            # breakpoint()
            # if text.contains('WeakSet'):

            if re.match(elem, text):
                return True
    return False

class ProxySystem:
    
    # def bind(self, obj): pass

    def wrap_int_to_ext(self, obj): return obj
    
    def wrap_ext_to_int(self, obj): return obj
    
    def on_int_call(self, func, *args, **kwargs):
        pass

    def on_ext_result(self, result):
        pass

    def on_ext_error(self, err_type, err_value, err_traceback):
        pass
        
    def on_ext_call(self, func, *args, **kwargs):
        pass

    # def stacktrace(self):
    #     self.tracer.stacktrace()

    def set_thread_id(self, id):
        utils.set_thread_id(id)

    @property
    def ext_apply(self): return functional.apply

    @property
    def int_apply(self): return functional.apply

    def wrap_weakref_callback(self, callback):
        def when_internal(f):
            return self.thread_state.dispatch(utils.noop, internal = f)
        
        return utils.observer(
            on_call = when_internal(self.on_weakref_callback_start),
            on_result = when_internal(self.on_weakref_callback_end),
            on_error = when_internal(self.on_weakref_callback_end),
            function = callback)

    def __init__(self, thread_state, immutable_types, tracer, traceargs):
        
        self.patched_types = set()
        self.thread_state = thread_state
        self.fork_counter = 0
        self.tracer = tracer
        self.immutable_types = immutable_types
        self.base_to_patched = {}

        def should_proxy_type(cls):
            return cls is not object and \
                   not issubclass(cls, tuple(immutable_types)) and \
                   cls not in self.patched_types

        should_proxy = functional.sequence(functional.typeof, functional.memoize_one_arg(should_proxy_type))

        def proxyfactory(proxytype):
            return functional.walker(functional.when(should_proxy, maybe_proxy(proxytype)))

        int_spec = SimpleNamespace(
            apply = thread_state.wrap('internal', self.int_apply),
            proxy = proxyfactory(thread_state.wrap('disabled', self.int_proxytype)),
            on_call = tracer('proxy.int.call', self.on_int_call),
            on_result = tracer('proxy.int.result'),
            on_error = tracer('proxy.int.error'),
        )
        
        def trace_ext_call(func, *args, **kwargs):
            self.on_ext_call(func, *args, **kwargs)
            self.checkpoint(self.normalize_for_checkpoint({'function': func, 'args': args, 'kwargs': kwargs}))

        ext_spec = SimpleNamespace(
            apply = thread_state.wrap('external', self.ext_apply),
            proxy = proxyfactory(thread_state.wrap('disabled', self.ext_proxytype)),

            on_call = trace_ext_call if traceargs else self.on_ext_call,
            on_result = self.on_ext_result,
            on_error = self.on_ext_error,
        )

        int2ext, ext2int = adapter_pair(int_spec, ext_spec)

        def gateway(name, internal = functional.apply, external = functional.apply):
            default = tracer(name, unproxy_execute)
            return thread_state.dispatch(default, internal = internal, external = external)

        self.ext_handler = thread_state.wrap('retrace', self.wrap_int_to_ext(int2ext))
        self.int_handler = thread_state.wrap('retrace', self.wrap_ext_to_int(ext2int))

        self.ext_dispatch = gateway('proxy.int.disabled.event', internal = self.ext_handler)
        self.int_dispatch = gateway('proxy.ext.disabled.event', external = self.int_handler)

        self.exclude_from_stacktrace(Tracer._write_call)

        # if 'systrace' in tracer.config:
        #     func = thread_state.wrap(desired_state = 'disabled', function = tracer.systrace)
        #     func = self.thread_state.dispatch(lambda *args: None, internal = func)
        #     sys.settrace(func)
        # self.on_new_patched = self.thread_state.dispatch(utils.noop, 
        #     internal = self.on_new_ext_patched, external = self.on_new_int_patched)
        # tracer.trace_calls(thread_state)

    def disable_for(self, func):
        return self.thread_state.wrap('disabled', func)
    
    def new_child_path(self, path):
        return path.parent / f'fork-{self.fork_counter}' / path.name

    def before_fork(self):
        self.saved_thread_state = self.thread_state.value
        self.thread_state.value = 'disabled'

    def after_fork_in_child(self):
        self.thread_state.value = self.saved_thread_state
        self.fork_counter = 0

    def after_fork_in_parent(self):
        self.thread_state.value = self.saved_thread_state
        self.fork_counter += 1

    def on_thread_exit(self, thread_id):
        pass

    # def create_stub(self): return False
        
    def int_proxytype(self, cls):
        if cls is object:
            breakpoint()

        return dynamic_int_proxytype(
                handler = self.int_dispatch,
                cls = cls,
                bind = self.bind)
        
    def ext_proxytype(self, cls):

        proxytype = dynamic_proxytype(handler = self.ext_dispatch, cls = cls)
        proxytype.__retrace_source__ = 'external'

        if issubclass(cls, Patched):
            patched = cls
        elif cls in self.base_to_patched:
            patched = self.base_to_patched[cls]
        else:
            patched = None

        assert patched == None or patched.__base__ is not object

        if patched:
            # breakpoint()

            patcher = getattr(patched, '__retrace_patch_proxy__', None)
            if patcher: patcher(proxytype)

            # for key,value in patched.__dict__.items():
            #     if callable(value) and key not in ['__new__'] and not hasattr(proxytype, key):
            #         setattr(proxytype, key, value)

        return proxytype
    
    def function_target(self, obj): return obj

    def proxy_function(self, func, **kwargs):
        if is_instance_method(func):
            return self.thread_state.method_dispatch(func, **kwargs)
        else:
            f = self.thread_state.dispatch(func, **kwargs)

            if isinstance(func, staticmethod):
                return staticmethod(f)
            elif isinstance(func, classmethod):
                return classmethod(f)
            else:
                return f

    def proxy_ext_function(self, func):
        proxied = utils.wrapped_function(handler = self.ext_handler, target = func)
        return self.proxy_function(func = func, internal = proxied)
    
    def proxy_int_function(self, func):
        proxied = utils.wrapped_function(handler = self.int_handler, target = func)
        return self.proxy_function(func = func, external = proxied)

    def proxy_ext_member(self, member):
        return utils.wrapped_member(handler = self.ext_dispatch, target = member)

    def proxy_int_member(self, member):
        return utils.wrapped_member(handler = self.int_dispatch, target = member)

    # def proxy__new__(self, *args, **kwargs):
    #     return self.ext_handler(*args, **kwargs)

    # def on_new_ext_patched(self, obj):
    #     print(f'HWE!!!!!!!!!!! 2')
    #     print(f'HWE!!!!!!!!!!! 3')
    #     return id(obj)

    # def on_new_int_patched(self, obj):
    #     return id(obj)

    # def on_del_patched(self, ref):
    #     pass

    # def create_from_external(self, obj):
    #     pass
    #     breakpoint()

    def patch_type(self, cls):

        # breakpoint()

        assert isinstance(cls, type)

        if issubclass(cls, BaseException): breakpoint()
        
        assert not issubclass(cls, BaseException)

        assert cls not in self.patched_types

        self.patched_types.add(cls)
        
        # if a method returns a patched object... what to do
        # well if its a subclass, may need to return id as may have local state
        # a subclass will have an associated id, should have been created via __new__
        # if not a subclass, create empty proxy, what about object identity?

        # track ALL creations ? override tp_alloc ? 
        # patch tp_alloc and tp_dealloc to call lifecycle object 
        # given a lifecycle, can attach to a class
        # have one function attach_lifecycle, on_new returns token passed to on_del function
        
        def proxy_attrs(cls, dict, proxy_function, proxy_member):

            blacklist = ['__new__', '__getattribute__', '__del__', '__dict__']

            for name, value in dict.items():
                if name not in blacklist:
                    if type(value) in [types.MemberDescriptorType, types.GetSetDescriptorType]:
                        setattr(cls, name, proxy_member(value))
                    elif callable(value):
                        setattr(cls, name, proxy_function(value))

        with WithoutFlags(cls, "Py_TPFLAGS_IMMUTABLETYPE"):

            proxy_attrs(
                cls,
                dict = superdict(cls),
                proxy_function = self.proxy_ext_function,
                proxy_member = self.proxy_ext_member)

            on_alloc = self.thread_state.dispatch(
                    utils.noop, 
                    internal = self.bind,
                    external = self.create_from_external)

            utils.set_on_alloc(cls, on_alloc)

            cls.__retrace_system__ = self

            if utils.is_extendable(cls):
                def init_subclass(cls, **kwargs):
                    
                    self.patched_types.add(cls)

                    def proxy_function(obj):
                        return utils.wrapped_function(handler = self.int_handler, target = obj)

                    proxy_attrs(
                        cls,
                        dict = cls.__dict__,
                        proxy_function = self.proxy_int_function,
                        proxy_member = self.proxy_int_member)
                    
                cls.__init_subclass__ = classmethod(init_subclass)

                for subtype in get_all_subtypes(cls):
                    with WithoutFlags(subtype, "Py_TPFLAGS_IMMUTABLETYPE"):
                        init_subclass(subtype)

            cls.__retrace__ = self

        self.bind(cls)

        return cls

    # def is_entry_frame(self, frame):
    #     return frame.globals.get("__name__", None) == "__main__"

    def proxy_value(self, obj):
        utils.sigtrap('proxy_value')

        proxytype = dynamic_proxytype(handler = self.ext_dispatch, cls = type(obj))
        proxytype.__retrace_source__ = 'external'

        return utils.create_wrapped(proxytype, obj)

    def __call__(self, obj):
        assert not isinstance(obj, BaseException)
        assert not isinstance(obj, Proxy)
        assert not isinstance(obj, utils.wrapped_function)
            
        if type(obj) == type:
            return self.patch_type(obj)
            
        elif type(obj) in self.immutable_types:
            return obj
        
        elif is_function_type(type(obj)): 
            return self.thread_state.dispatch(obj, internal = self.proxy_ext_function(obj))
        
        elif type(obj) == types.ClassMethodDescriptorType:
            func = self.thread_state.dispatch(obj, internal = self.proxy_ext_function(obj))
            return classmethod(func)
        else:
            return self.proxy_value(obj)

    # def write_trace(self, obj):

    # exclude = set([
    #     "ABCMeta.__subclasscheck__"
    #     # "__subclasscheck__"
    #     ])


        
