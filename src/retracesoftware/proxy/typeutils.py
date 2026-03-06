# from retrace_utils import _intercept
import retracesoftware.utils as utils

def flags(cls : type):
    f = utils.type_flags(cls)

    s = set()

    for name,value in utils.TypeFlags.items():
        if (f & value) != 0:
            s.add(name)
        f = f & ~value

    if f != 0:
        s.add(f)

    return s

class WithoutFlags:

    def __init__(self, cls  , *flags):
        self.cls = cls
        self.flags = flags

    def __enter__(self):
        self.saved = utils.type_flags(self.cls)
        flags = self.saved

        for flag in self.flags:
            flags = flags & ~utils.TypeFlags[flag]

        utils.set_type_flags(self.cls, flags)
        return self.cls

    def __exit__(self, *args):
        utils.set_type_flags(self.cls, self.saved)

class WithFlags:

    def __init__(self, cls  , *flags):
        self.cls = cls
        self.flags = flags

    def __enter__(self):
        self.saved = utils.type_flags(self.cls)
        flags = self.saved

        for flag in self.flags:
            flags |= utils.TypeFlags[flag]

        utils.set_type_flags(self.cls, flags)
        return self.cls

    def __exit__(self, *args):
        utils.set_type_flags(self.cls, self.saved)

def add_flag(cls : type, flag):
    if isinstance(flag, str):
        flag = utils.TypeFlags[flag]
    
    flags = utils.type_flags(cls)
    utils.set_type_flags(cls, flags | flag)

def modify(cls):
    return WithoutFlags(cls, "Py_TPFLAGS_IMMUTABLETYPE")


def type_has_feature(cls, flag):
    if isinstance(flag, str):
        flag = utils.TypeFlags[flag]
    return (utils.type_flags(cls) & flag) != 0

def type_disallow_instantiation(cls : type):
    return utils.type_flags(cls) & utils.Py_TPFLAGS_DISALLOW_INSTANTIATION

def is_method_descriptor(cls : type):
    return (utils.type_flags(cls) & utils.TypeFlags["Py_TPFLAGS_METHOD_DESCRIPTOR"]) != 0

def extend_type(cls : type):
    subclasses = list(cls.__subclasses__())

    utils.make_extensible(cls)

    print(f'extending: {cls}')
    
    extended = utils.extend_type(cls)

    for subclass in subclasses:
        print(f"updating subclass: {subclass}")
        subclass.__bases__ = tuple(map(lambda x: extended if x is cls else x, subclass.__bases__))        

    return extended
