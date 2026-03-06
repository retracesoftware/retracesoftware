"""
RecordSystem: given an external gate, an internal gate, and record specs,
builds the int2ext/ext2int adapters and installs them onto the gates.

The record system does not create the gates; it receives them and installs
its behavior (record mode) onto them. Same gates can be used by a different
consumer (e.g. replay) by setting different executors.
"""

from retracesoftware.proxy.gateway import adapter_pair
import retracesoftware.functional as functional

class RecordSystem:
    """
    Installs record-mode behavior onto a pair of gates.

    Receives:
      - ext_gate: gate for external-origin calls (int→ext)
      - int_gate: gate for internal-origin calls (ext→int)
      - int_spec, ext_spec: namespaces with .proxy, .on_call, .on_result, .on_error
        (and any other keys adapter_pair / adapter expect)

    Builds int2ext and ext2int via adapter_pair(ext_gate, int_spec, ext_spec),
    then sets ext_gate.set(int2ext) and int_gate.set(ext2int).

    Exposes .int2ext and .ext2int so the rest of the system (e.g. ProxySystem)
    can use them for binding and patching.
    """

    __slots__ = ("ext_gate", "int_gate", "int2ext", "ext2int")

    def __init__(self, ext_gate, int_gate, *, int_spec, ext_spec):
        self.ext_gate = ext_gate
        self.int_gate = int_gate

        int2ext, ext2int = adapter_pair(ext_gate, int_spec, ext_spec)

        # int2ext and ext2int are two executor functions bound by the gates.
        # the adapter_pair should be a type which is installable. 
        self.int2ext = int2ext
        self.ext2int = ext2int

        ext_gate.set(int2ext)
        
        int_gate.set(functional.if_then_else(ext_gate.test(ext2int), ext2int, functional.apply))


    def create_int_gate(self, ext_gate):
        # int gate state is based off ext_gate state
        pass

    def disable(self):
        """Turn off record mode for this thread (both gates passthrough)."""
        self.ext_gate.disable()
        self.int_gate.disable()

    def enable(self):
        """Turn record mode back on for this thread."""
        self.ext_gate.set(self.int2ext)
        self.int_gate.set(self.ext2int)
