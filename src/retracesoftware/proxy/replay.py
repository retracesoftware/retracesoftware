import retracesoftware.functional as functional
import retracesoftware.utils as utils

from retracesoftware.install.tracer import Tracer
from retracesoftware.proxy.thread import per_thread_messages, thread_id
from retracesoftware.proxy.messagestream import *
from retracesoftware.proxy.proxytype import *
# from retracesoftware.proxy.gateway import gateway_pair
from retracesoftware.proxy.record import StubRef
from retracesoftware.proxy.proxysystem import ProxySystem
from retracesoftware.proxy.stubfactory import StubFactory
from retracesoftware.proxy.globalref import GlobalRef

import os

class ReplayProxySystem(ProxySystem):
    
    def after_fork_in_child(self):
        self.reader.path = self.new_child_path(self.reader.path)
        super().after_fork_in_child()

    # def dynamic_ext_proxytype(self, cls):
    #     raise Exception('dynamic_ext_proxytype should not be called in replay')

    @property
    def ext_apply(self): 
        return functional.repeatedly(self.next_result)

    def proxy__new__(self, __new__, *args, **kwargs):
        func = functional.repeatedly(self.next_result)
        func.__name__ = '__new__'
        return super().proxy__new__(func, *args, **kwargs)

    def basetype(self, cls):
        return self.stub_factory.create_stubtype(StubRef(cls))

    def trace_writer(self, name, *args):
        with self.thread_state.select('disabled'):
            # read = self.messages_read

            self.read_required('TRACE')
            # self.read_required(read)
            self.read_required(name)

            if name == 'stacktrace':
                print('FOOO!!!')
                os._exit(1)
                record = self.readnext()
                if args[0] == record:
                    self.last_matching_stack = args[0]
                else:
                    on_stack_mismatch(
                        last_matching = self.last_matching_stack,
                        record = record,
                        replay = args[0])
                    os._exit(1)
            else:
                # print(f'Trace: {self.reader.messages_read} {name} {args}')
                for arg in args:
                    self.read_required(arg)

    def on_thread_exit(self, thread_id):
        # print(f'on_thread_exit!!!!')
        self.reader.wake_pending()

    def __init__(self, 
                 reader,
                 thread_state,
                 immutable_types,
                 tracing_config,
                 traceargs,
                 verbose = False,
                 fork_path = [],
                 skip_weakref_callbacks = False):

        self.reader = reader
        # self.skip_weakref_callbacks = skip_weakref_callbacks

        self.fork_path = fork_path

        self.messages = MessageStream(
            thread_state = thread_state,
            source = reader, 
            skip_weakref_callbacks = skip_weakref_callbacks,
            verbose = verbose)

        self.checkpoint = self.messages.checkpoint
        self.bind = self.messages.bind
        self.next_result = self.messages.result
        self.exclude_from_stacktrace = self.messages.excludes.add

        self.stub_factory = StubFactory(thread_state = thread_state, next_result = self.next_result)

        self.last_matching_stack = None

        def run_ref(ref):
            print(f'run_ref!!!! {ref}')
            return ref()

        self.reader.type_deserializer[StubRef] = self.stub_factory
        # self.reader.type_deserializer[GlobalRef] = lambda ref: ref()
        self.reader.type_deserializer[GlobalRef] = run_ref

        excludes = [ReplayProxySystem.trace_writer]
        
        for exclude in excludes:
            self.messages.excludes.add(exclude)

        sync = functional.lazy(self.messages.read_required, 'SYNC')

        read_sync = thread_state.dispatch(utils.noop, internal = sync)

        self.on_ext_call = sync

        self.sync = lambda function: utils.observer(on_call = read_sync, function = function)
        
        self.create_from_external = utils.noop

        if skip_weakref_callbacks:
            self.wrap_weakref_callback = \
                lambda callback: \
                    thread_state.dispatch(
                        callback, internal = self.disable_for(callback))
        else:
            self.on_weakref_callback_start = functional.lazy(self.messages.read_required, 'ON_WEAKREF_CALLBACK_START')
            self.on_weakref_callback_end = functional.lazy(self.messages.read_required, 'ON_WEAKREF_CALLBACK_END')

        super().__init__(thread_state = thread_state, 
                         tracer = Tracer(tracing_config, writer = self.trace_writer),
                         immutable_types = immutable_types,
                         traceargs = traceargs)

    def write_trace(self, obj):
        if 'TRACER' != self.messages():
            utils.sigtrap(obj)

        # self.read_required       ('TRACER')
        self.read_required(obj)
