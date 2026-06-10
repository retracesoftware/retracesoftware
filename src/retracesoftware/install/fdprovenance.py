"""Track file descriptors created by passthrough filesystem calls."""

from __future__ import annotations

import threading


class FdProvenance:
    """Small process-local fd provenance tracker.

    Path-backed file operations may intentionally bypass Retrace. If those
    operations return a file descriptor, fd-level operations on that descriptor
    must stay in the same passthrough island.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._passthrough_fds: set[int] = set()

    def should_retrace_fd(self, fd: int) -> bool:
        with self._lock:
            return fd not in self._passthrough_fds

    def should_passthrough_call(self, module_name, function_name, args, kwargs) -> bool:
        if module_name != "posix":
            return False
        if function_name != "dup":
            return False

        fd = _arg(args, kwargs, "fd", 0)
        return fd in {0, 1, 2}

    def mark_result(self, result):
        fd = _result_fd(result)
        if fd is not None:
            with self._lock:
                self._passthrough_fds.add(fd)
        return result

    def observe_passthrough_call(self, module_name, function_name, args, kwargs, result):
        if module_name == "_io" and function_name in {"open", "open_code"}:
            return self.mark_result(result)

        if module_name != "posix":
            return result

        if function_name == "open":
            return self.mark_result(result)

        if function_name == "dup":
            source_fd = _arg(args, kwargs, "fd", 0)
            if source_fd in {0, 1, 2} or _is_passthrough_fd(source_fd, self):
                return self.mark_result(result)
            return result

        if function_name == "dup2":
            source_fd = _arg(args, kwargs, "fd", 0)
            target_fd = _arg(args, kwargs, "fd2", 1)
            if _is_passthrough_fd(source_fd, self) and isinstance(target_fd, int):
                with self._lock:
                    self._passthrough_fds.add(target_fd)
            return result

        if function_name == "close":
            fd = _arg(args, kwargs, "fd", 0)
            if isinstance(fd, int):
                with self._lock:
                    self._passthrough_fds.discard(fd)
            return result

        return result


def _arg(args, kwargs, name, index):
    if name in kwargs:
        return kwargs[name]
    if len(args) > index:
        return args[index]
    return None


def _is_passthrough_fd(fd, provenance: FdProvenance) -> bool:
    return isinstance(fd, int) and not provenance.should_retrace_fd(fd)


def _result_fd(result) -> int | None:
    if isinstance(result, int) and result >= 0:
        return result

    fileno = getattr(result, "fileno", None)
    if fileno is None:
        return None

    try:
        fd = fileno()
    except Exception:
        return None

    if isinstance(fd, int) and fd >= 0:
        return fd
    return None
