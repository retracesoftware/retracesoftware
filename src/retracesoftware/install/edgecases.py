# from .proxytype import *

import functools
import os

from retracesoftware.install import globals

def recvfrom_into(target):
    @functools.wraps(target)
    def wrapper(self, buffer, nbytes = 0, flags = 0):
        data, address = self.recvfrom(len(buffer) if nbytes == 0 else nbytes, flags)
        buffer[0:len(data)] = data
        return len(data), address
    return wrapper

def recv_into(target):
    @functools.wraps(target)
    def wrapper(self, buffer, nbytes = 0, flags = 0):
        data = self.recv(len(buffer) if nbytes == 0 else nbytes, flags)
        buffer[0:len(data)] = data
        return len(data)
    return wrapper

def recvmsg_into(target):
    @functools.wraps(target)
    def wrapper(self, buffers, ancbufsize = 0, flags = 0):
        raise NotImplementedError('TODO')
    return wrapper

def read(target):
    @functools.wraps(target)
    def wrapper(self, *args):
        # super_type = super(type(self), self)

        if len(args) == 0:
            return target(self)
        else:
            buflen = args[0]

            # pdb.set_trace()

            data = target(self, buflen)

            if len(args) == 1:
                return data
            else:
                buffer = args[1]

                buffer[0:len(data)] = data

                return len(data)
    return wrapper

def write(target):
    @functools.wraps(target)
    def wrapper(self, byteslike):
        return target(byteslike.tobytes())

    return wrapper

def readinto(target):
    @functools.wraps(target)
    def wrapper(self, buffer):
        bytes = self.read(buffer.nbytes)
        buffer[:len(bytes)] = bytes
        return len(bytes)
    return wrapper

typewrappers = {
    '_socket': {
        'socket': {
            'recvfrom_into': recvfrom_into,
            'recv_into': recv_into,
            'recvmsg_into': recvmsg_into
        }
    },
    '_ssl': {
        '_SSLSocket': {
            'read': read,
            # 'write': write
        }
    },
    'io': {
        'FileIO': {
            'readinto': readinto 
        },
        'BufferedReader': {
            'readinto': readinto
        },
        'BufferedRandom': {
            'readinto': readinto
        }
    }
}

def patchtype(module, name, cls : type):
    if module in typewrappers:
        if name in typewrappers[module]:
            for method,patcher in typewrappers[module][name].items():
                setattr(cls, method, patcher(getattr(cls, method)))

def transform_argument(target : callable, position : int, name : str, transform : callable, default = None):
    assert callable(transform)
    assert callable(target)

    @functools.wraps(target)
    def wrapper(*args, **kwargs):
        if name in kwargs:
            kwargs[name] = transform(kwargs[name])
        elif len(args) > position:
            args = list(args)
            args[position] = transform(args[position])
        else:
            kwargs[name] = transform(default)

        # print(f'Running: {args} {kwargs}')
        return target(*args, **kwargs)

    return wrapper 

subprocess_counter = 0
recording_path = None

def next_subprocess_path():
    global subprocess_counter
    path = f'subprocess-{subprocess_counter}'
    subprocess_counter += 1
    return recording_path / path

def retrace_env():

    env = {
        'RETRACE_RECORDING_PATH': str(next_subprocess_path()),
        # 'RETRACE_PARENT_THREAD_ID': threading.current_thread().__retrace_thread_id__,
        # 'RETRACE_PARENT_EXEC_COUNTER': str(next_exec_counter()
        'RETRACE': 'true'
        # 'RETRACE_MODE': 'proxy' if os.getenv('RETRACE_MODE') == 'proxy' else 'record'
    }
    # a timestamped directory off current directory
    # controlled by config
    # 1. if recording_dir is set use that
    # 2. if recording_dir is not set look for 

    # env['RETRACE_EXECUTION_ID'] = hashlib.md5(json.dumps(env).encode('UTF8')).hexdigest()

    return env

def fork_exec(target):

    def transform(env):
        r = [f'{k}={v}'.encode('utf-8') for k,v in retrace_env().items()]
        return env + r if env else r

    return transform_argument(target = target, 
                              position = 5,
                              name = 'env',
                              transform = transform, default = os.environ)

    # import threading
    # thread_state = threading.current_thread().__retrace__.thread_state

    # patched = transform_argument(target = target, 
    #                           position = 5,
    #                           name = 'env',
    #                           transform = transform, default = os.environ)

    # return thread_state.dispatch(target, internal = patched)

def posix_spawn(target):

    def transform(env):
        r = retrace_env()
        return {**env, **r}

    return transform_argument(target = target, 
                              position = 2,
                              name = 'env',
                              transform = transform, default = os.environ)

    # import threading
    # thread_state = threading.current_thread().__retrace__.thread_state

    # patched = transform_argument(target = target, 
    #                           position = 2,
    #                           name = 'env',
    #                           transform = transform, default = os.environ)

    # return thread_state.dispatch(target, internal = patched)

function_patchers = {
    '_posixsubprocess': {
        'fork_exec': fork_exec
    },
    'posix': {
        'posix_spawn': posix_spawn
    }
}

# import traceback

def typepatcher(cls : type):

    # print(f'!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! type: {cls} created')
    # traceback.print_stack()
    
    return typewrappers.get(cls.__module__, {}).get(cls.__name__, {})

    # if cls.__module__ in typewrappers:
    #     # print(f'!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! TYPEWRAPPER for {cls}')

    #     mod = typewrappers[cls.__module__]

    #     return mod.get(cls.__name__, {})
    
    #     if cls.__name__ in mod:
    #         # if cls.__name__ == '_SSLSocket':
    #         #     breakpoint()

    #         log.info("Applying specialized typewrapper to %s, updated slots: %s", cls, list(mod[cls.__name__].keys()))

    #         for name,value in mod[cls.__name__].items():
    #             setattr(cls, name, value(getattr(cls, name)))

    #         # slots = {'__module__': cls.__module__, '__slots__': ()}
    #         # slots.update(mod[cls.__name__])
    #         # return type(cls.__name__, (cls, ), slots)

    # return cls

def typewrapper(cls : type):

    # print(f'!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! type: {cls} created')
    # traceback.print_stack()
    
    if cls.__module__ in typewrappers:
        # print(f'!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!! TYPEWRAPPER for {cls}')

        mod = typewrappers[cls.__module__]

        if cls.__name__ in mod:
            # if cls.__name__ == '_SSLSocket':
            #     breakpoint()

            log.info("Applying specialized typewrapper to %s, updated slots: %s", classname(cls), list(mod[cls.__name__].keys()))

            slots = {'__module__': cls.__module__, '__slots__': ()}
            slots.update(mod[cls.__name__])
            return type(cls.__name__, (cls, ), slots)

    return cls
