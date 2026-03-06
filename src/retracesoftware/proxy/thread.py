import retracesoftware.functional as functional
import retracesoftware.utils as utils

import os
import _thread

# def thread_aware_writer(writer):
#     on_thread_switch = functional.sequence(utils.thread_id(), writer.handle('THREAD_SWITCH'))
#     return utils.threadawareproxy(on_thread_switch = on_thread_switch, target = writer)

class ThreadSwitch:
    __slots__ = ['id']

    def __init__(self, id):
        self.id = id

    def __repr__(self):
        return f'ThreadSwitch<{self.id}>'

    def __str__(self):
        return f'ThreadSwitch<{self.id}>'

# def set_thread_id(writer, id):
#     utils.sigtrap(id)
#     utils.set_thread_id(writer.handle(ThreadSwitch(id)))

def write_thread_switch(writer):
    on_thread_switch = functional.repeatedly(functional.sequence(utils.thread_id, writer))

    return lambda f: utils.thread_aware_proxy(target = f, on_thread_switch = on_thread_switch, sticky = False)

def prefix_with_thread_id(f, thread_id):
    current = None

    def next():
        nonlocal current, f
        if current is None: current = thread_id()

        obj = f()

        while issubclass(type(obj), ThreadSwitch):
            current = obj.id
            obj = f()

        # print(f'prefix_with_thread_id: {(current, obj)}')
        return (current, obj)

    return next

def per_thread_messages(messages):
    thread_id = utils.thread_id
    # thread_id = lambda: 'FOOOOO!!!'

    def on_timeout(demux, key):
        print(f'ON TIMEOUT!!!! {key} pending: {demux.pending} {demux.pending_keys}')
        utils.sigtrap(demux)
        os._exit(1)

    demux = utils.demux(source = prefix_with_thread_id(messages, thread_id),
                        key_function = lambda obj: obj[0],
                        timeout_seconds = 60,
                        on_timeout = on_timeout)

    # def next():
    #     thread,message = demux(thread_id())
    #     return message
    
    # return next
    return functional.repeatedly(lambda: demux(thread_id())[1])


# _thread.start_new_thread(function, args[, kwargs])
counters = _thread._local()
counters.id = ()
counters.counter = 0

def with_thread_id(thread_id, on_exit, function):
    def on_call(*args, **kwargs):
        counters.id = thread_id
        counters.counter = 0

    def on_result(res):
        on_exit(thread_id)
    
    def on_error(*args):
        on_exit(thread_id)

    return utils.observer(on_call = on_call, on_result = on_result, on_error = on_error, function = function)

thread_id = functional.lazy(getattr, counters, 'id')

def start_new_thread_wrapper(thread_state, on_exit, start_new_thread):

    def wrapper(function, *args):
        
        next_id = counters.id + (counters.counter,)
        counters.counter += 1

        wrapped_function = with_thread_id(thread_id = next_id,
                                          on_exit = on_exit, 
                                          function = thread_state.wrap('internal', function))

        return start_new_thread(wrapped_function, *args)

    return thread_state.dispatch(start_new_thread, internal = wrapper)

