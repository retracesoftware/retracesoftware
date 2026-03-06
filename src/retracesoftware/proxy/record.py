import retracesoftware.functional as functional
import retracesoftware.utils as utils
import retracesoftware.stream as stream

from retracesoftware.proxy.proxytype import *
# from retracesoftware.proxy.gateway import gateway_pair
from retracesoftware.proxy.proxysystem import ProxySystem
from retracesoftware.proxy.thread import write_thread_switch, ThreadSwitch, thread_id
from retracesoftware.install.tracer import Tracer
from retracesoftware.install.patchfindspec import patch_find_spec
from retracesoftware.proxy.stubfactory import StubRef, ExtendedRef
from retracesoftware.proxy.globalref import GlobalRef

import sys
import os
import types
import gc

# class Placeholder:
#     __slots__ = ['id', '__weakref__']

#     def __init__(self, id):
#         self.id = id
    
def keys_where_value(pred, dict):
    for key,value in dict.items():
        if pred(value): yield key

types_lookup = {v:k for k,v in types.__dict__.items() if isinstance(v, type)}

def resolve(obj):
    try:
        return getattr(sys.modules[obj.__module__], obj.__name__)
    except:
        return None
    
def resolveable(obj):
    try:
        return getattr(sys.modules[obj.__module__], obj.__name__) is obj
    except:
        return False

def resolveable_name(obj):
    if obj in types_lookup:
        return ('types', types_lookup[obj])
    elif resolve(obj) is obj:
        return (obj.__module__, obj.__name__)
    else:
        return None

# when 
class RecordProxySystem(ProxySystem):
    
    # def bind(self, obj):
    #     self.bindings[obj] = self.writer.handle(Placeholder(self.next_placeholder_id))
    #     self.writer(self.bindings[obj])
    #     self.next_placeholder_id += 1

    def before_fork(self):
        self.writer.keep_open = False
        # self.writer.close()
        super().before_fork()
        # self.writer.path = self.dynamic_path

    def after_fork_in_child(self):
        new_path = self.new_child_path(self.writer.path)
        new_path.parent.mkdir()
        self.writer.path = new_path
        self.writer.keep_open = True
        super().after_fork_in_child()

    def after_fork_in_parent(self):
        super().after_fork_in_parent()
        self.thread_state.value = self.saved_thread_state
        self.writer.keep_open = True
        # self.writer.reopen()
    
    def set_thread_id(self, id):
        utils.set_thread_id(self.writer.handle(ThreadSwitch(id)))
        # utils.set_thread_id(id)

    # def is_entry_frame(self, frame):
    #     if super().is_entry_frame(frame):
    #         self.write_main_path(frame.function.__code__.co_filename)
    #         return True
    #     return False

    def create_from_external(self, obj):
        # class of obj is bound
        # can now write type(obj)

        self.bind(obj)

        breakpoint()

    def patch_type(self, cls):

        patched = super().patch_type(cls)

        self.writer.type_serializer[patched] = functional.side_effect(self.writer.ext_bind)

        return patched

    def exclude_from_stacktrace(self, func):
        self.writer.exclude_from_stacktrace(func)

    def on_gc_event(self, phase, info):
        if phase == 'start':
            self.writer.stacktraces = False
            self.on_start_collect(info['generation'])
            
        elif phase == 'stop':
            self.on_end_collect()
            self.writer.stacktraces = self.stacktraces

    def __init__(self, thread_state,
                 writer,
                 immutable_types, 
                 tracing_config,
                 maybe_collect,
                 traceargs):
        
        self.fork_counter = 0
        # self.write_main_path = write_main_path
    
        self.getpid = thread_state.wrap(
            desired_state = 'disabled', function = os.getpid)
        
        self.pid = self.getpid()

        self.writer = writer

        self.stacktraces = self.writer.stacktraces

        if self.stacktraces:
            def set(status):
                self.writer.stacktraces = status

            self.wrap_weakref_callback = functional.sequence(
                self.wrap_weakref_callback,
                lambda callback:           
                    utils.observer(
                        on_call = functional.lazy(set, False),
                        on_result = functional.lazy(set, True),
                        on_error = functional.lazy(set, True),
                        function = callback))
                
        self.exclude_from_stacktrace(RecordProxySystem.patch_type)
        self.exclude_from_stacktrace(patch_find_spec.__call__)

        self.extended_types = {}

        sync_handle = self.writer.handle('SYNC')

        self.on_ext_call = functional.lazy(utils.runall(maybe_collect, sync_handle) if maybe_collect else sync_handle) 

        write_sync = thread_state.dispatch(utils.noop, internal = functional.lazy(sync_handle))

        self.sync = lambda function: \
            utils.observer(on_call = write_sync, function = function)
        error = self.writer.handle('ERROR')

        def write_error(cls, val, traceback):
            assert isinstance(val, BaseException)
            # if not isinstance(val, BaseException):
            #     # Debug: something is passing wrong value type
            #     import sys
            #     print(f"DEBUG write_error called with:")
            #     print(f"  cls={cls}, type={type(cls)}")
            #     print(f"  val={val!r}, type={type(val)}")
            #     print(f"  traceback={traceback}")
            #     print(f"  sys.exc_info()={sys.exc_info()}")
                
            #     # Some C extensions (e.g. psycopg2) return plain strings as errors
            #     # Wrap them in the exception class if possible
            #     if isinstance(val, str) and isinstance(cls, type) and issubclass(cls, BaseException):
            #         val = cls(val)
            #     else:
            #         val = RuntimeError(f"Non-exception error: {val}")
            error(cls, val)
        
        tracer = Tracer(tracing_config, writer = self.writer.handle('TRACE'))

        self.writer.exclude_from_stacktrace(write_error)
        # self.writer.exclude_from_stacktrace(Tracer.write_call)
        
        self.on_int_call = self.writer.handle('CALL')
        
        # write_new_ref = self.writer.handle('RESULT')

        self.on_ext_result = self.writer.handle('RESULT')
        
        self.on_ext_error = write_error

        self.writer.type_serializer[types.ModuleType] = GlobalRef

        self.bind = self.writer.bind

        self.create_from_external = self.writer.ext_bind

        self.write_trace = self.writer.handle('TRACER')

        self.checkpoint = self.writer.handle('CHECKPOINT')

        self.on_weakref_callback_start = functional.lazy(self.writer.handle('ON_WEAKREF_CALLBACK_START'))
        self.on_weakref_callback_end = functional.lazy(self.writer.handle('ON_WEAKREF_CALLBACK_END'))

        self.on_start_collect = self.writer.handle('ON_START_COLLECT')
        self.on_end_collect = self.writer.handle('ON_END_COLLECT')

        super().__init__(thread_state = thread_state, 
                         tracer = tracer, 
                         traceargs = traceargs,
                         immutable_types = immutable_types)

    def ext_proxytype(self, cls):
        
        proxytype = super().ext_proxytype(cls)

        ref = self.writer.handle(StubRef(proxytype))

        self.writer.type_serializer[proxytype] = functional.constantly(ref)

        return proxytype


