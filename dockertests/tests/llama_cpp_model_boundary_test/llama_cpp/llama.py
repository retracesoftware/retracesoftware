import ctypes
import os

_LIBC = ctypes.CDLL(None)
_LIBC.getpid.restype = ctypes.c_int


def _fail_if_live_replay(operation):
    native_pid = int(_LIBC.getpid())
    retraced_pid = os.getpid()
    if native_pid != retraced_pid:
        raise RuntimeError(
            f"live {operation} during replay: "
            f"native_pid={native_pid} retraced_pid={retraced_pid}"
        )


class Llama:
    def __init__(self, model_path, **kwargs):
        _fail_if_live_replay("Llama.__init__")
        self.model_path = model_path

    def create_chat_completion(self, messages, **kwargs):
        _fail_if_live_replay("Llama.create_chat_completion")
        return {
            "choices": [
                {"message": {"content": "cheapest-flight:LH1901"}}
            ]
        }
