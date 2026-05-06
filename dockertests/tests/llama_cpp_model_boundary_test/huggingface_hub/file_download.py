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


def hf_hub_download(repo_id, filename, **kwargs):
    _fail_if_live_replay("hf_hub_download")
    safe_repo = repo_id.replace("/", "--")
    return f"/fake-cache/{safe_repo}/{filename}"
