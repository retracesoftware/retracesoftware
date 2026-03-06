from retracesoftware import functional
from retracesoftware import utils

import _thread

# _thread.start_new_thread(function, args[, kwargs])

# push a per thread executor function, as a context manager

counters = _thread._local()
counters.id = ()
counters.counter = 0

def with_thread_id(thread_id, function, *args, **kwargs):
    counters.id = thread_id
    counters.counter = 0
    return function(*args, **kwargs)

thread_id = functional.lazy(getattr, counters, 'id')

def start_new_thread(original, wrapper, function, *args):
    return original(wrapper(function), *args)

def wrap_thread_function(thread_state, function):
    if thread_state.value == 'internal':
        next_id = counters.id + (counters.counter,)
        counters.counter += 1
        return functional.partial(with_thread_id, next_id, thread_state.wrap('internal', function))
    else:
        return function

def patch_thread_start(thread_state):

    wrapper = functional.partial(wrap_thread_function, thread_state)

    _thread.start_new = functional.partial(start_new_thread, _thread.start_new, wrapper)
    _thread.start_new_thread = functional.partial(start_new_thread, _thread.start_new_thread, wrapper)

    import threading
    threading._start_new_thread = _thread.start_new_thread
