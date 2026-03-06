import types
import sys

def find_module_name(mod):
    for name,value in sys.modules.items():
        if value is mod:
            return name
    
    raise Exception(f"Cannot create GlobalRef as module {mod} not found in sys.modules")

class GlobalRef:
    __slots__ = ['parts']

    def __init__(self, obj):
        if isinstance(obj, types.ModuleType):
            self.parts = (find_module_name(obj),)
        else:
            raise Exception(f"Cannot create GlobalRef from {obj}")

    def __call__(self):
        module = self.parts[0]

        if len(self.parts) == 0:
            return module
        else:
            obj = getattr(module, self.parts[1])
            
            if len(self.parts) == 1:
                return obj
            else:
                raise Exception(f"TODO")