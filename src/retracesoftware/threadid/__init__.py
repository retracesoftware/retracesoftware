from retracesoftware import utils
from retracesoftware import functional

def inc(x): return x + 1

class ThreadId:

    __slots__ = ('id', 'counter')

    def __init__(self):
        self.id = utils.ThreadLocal(None)
        self.id.set(())

        self.counter = utils.ThreadLocal(0)

    def __call__(self):
        return self.id.get()

    def next_id(self):
        counter = self.counter.update(inc)
        return self() + (counter,)

    def wrap_thread_function(self, function):
        if self() is None:
            return function

        next_id = self.next_id()

        def in_child(*args, **kwargs):
            self.id.set(next_id)

        return utils.observer(
            on_call = in_child, 
            function = function)

    def wrap_start_new_thread(self, original_start_new_thread):
        return functional.positional_param_transform(
            function = original_start_new_thread, 
            index = 0,
            transform = self.wrap_thread_function)
