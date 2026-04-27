# from .proxytype import *

import functools

from retracesoftware.install import globals
import retracesoftware.utils as utils

def recvfrom_into(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffer, nbytes = 0, flags = 0):
        data, address = self.recvfrom(len(buffer) if nbytes == 0 else nbytes, flags)
        buffer[0:len(data)] = data
        return len(data), address
    return wrapper

def recv_into(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffer, nbytes = 0, flags = 0):
        data = self.recv(len(buffer) if nbytes == 0 else nbytes, flags)
        buffer[0:len(data)] = data
        return len(data)
    return wrapper

def recvmsg_into(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffers, ancbufsize = 0, flags = 0):
        raise NotImplementedError('TODO')
    return wrapper

def read(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
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
    @utils.exclude_from_stacktrace
    def wrapper(self, byteslike):
        return target(byteslike.tobytes())

    return wrapper

def readinto(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffer):
        bytes = self.read(buffer.nbytes)
        buffer[:len(bytes)] = bytes
        return len(bytes)
    return wrapper

def readinto1(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffer):
        bytes = self.read1(buffer.nbytes)
        buffer[:len(bytes)] = bytes
        return len(bytes)
    return wrapper

def mmap_readinto(target):
    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)
    return wrapper

def openssl_set_verify(target):
    target = utils.try_unwrap(target)

    @functools.wraps(target)
    @utils.exclude_from_stacktrace
    def wrapper(self, mode, callback=None):
        if not hasattr(self, "_used"):
            return None
        if callback is not None:
            callback = utils.try_unwrap(callback)
        return target(self, mode, callback)
    return wrapper

def openssl_connection_class(cls):
    class Connection(cls):
        __module__ = cls.__module__
        __qualname__ = cls.__qualname__

        def close(self):
            try:
                sock = object.__getattribute__(self, "_socket")
            except AttributeError:
                return None
            if sock is None:
                return None

            import _socket

            if isinstance(sock, _socket.socket):
                try:
                    sock._closed = True
                except Exception:
                    pass
                try:
                    io_refs = sock._io_refs
                except Exception:
                    io_refs = 0
                if io_refs <= 0:
                    return utils.try_unwrap_apply(_socket.socket.close, sock)
                return None

            return utils.try_unwrap_apply(getattr(sock, "close"))

    Connection.__name__ = cls.__name__
    return Connection

typewrappers = {
    '_socket': {
        'socket': {
            'recvfrom_into': recvfrom_into,
            'recv_into': recv_into,
            'recvmsg_into': recvmsg_into
        }
    },
    'socket': {
        'SocketIO': {
            'readinto': readinto
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
            'readinto': readinto,
            'readinto1': readinto1
        },
        'BufferedRandom': {
            'readinto': readinto,
            'readinto1': readinto1
        },
        'BufferedRWPair': {
            'readinto': readinto,
            'readinto1': readinto1
        }
    },
    'mmap': {
        'mmap': {
            'readinto': mmap_readinto
        }
    }
}

def patchtype(module, name, cls : type):
    if module in typewrappers:
        if name in typewrappers[module]:
            for method,patcher in typewrappers[module][name].items():
                setattr(cls, method, patcher(getattr(cls, method)))

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
