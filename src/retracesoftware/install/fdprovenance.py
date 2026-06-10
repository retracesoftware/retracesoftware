"""Track file descriptors created by passthrough filesystem calls."""

from __future__ import annotations

import weakref


class FdProvenance:
    """Small process-local fd provenance tracker.

    Path-backed file operations may intentionally bypass Retrace. If those
    operations return a file descriptor, fd-level operations on that descriptor
    must stay in the same passthrough island.
    """

    def __init__(self) -> None:
        # This runs inside the install/pathpredicate plumbing itself, so avoid
        # Python locks here: lock methods are also part of Retrace's patched
        # surface. The GIL is enough for this small process-local table.
        self._passthrough_fds: dict[int, weakref.ReferenceType | None] = {}

    def should_retrace_fd(self, fd: int) -> bool:
        owner_ref = self._passthrough_fds.get(fd)
        if fd not in self._passthrough_fds:
            return True

        if owner_ref is None:
            return False

        owner = owner_ref()
        if owner is None or getattr(owner, "closed", False):
            self._passthrough_fds.pop(fd, None)
            return True

        try:
            current_fd = owner.fileno()
        except Exception:
            self._passthrough_fds.pop(fd, None)
            return True

        if current_fd != fd:
            self._passthrough_fds.pop(fd, None)
            return True

        return False

    def should_passthrough_call(self, module_name, function_name, args, kwargs) -> bool:
        if module_name != "posix":
            return False
        if function_name != "dup":
            return False

        fd = _arg(args, kwargs, "fd", 0)
        return fd in {0, 1, 2}

    def mark_result(self, result):
        fd, owner = _result_fd_and_owner(result)
        if fd is not None:
            self._passthrough_fds[fd] = _owner_ref(owner, self, fd)
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
                self._passthrough_fds[target_fd] = None
            return result

        if function_name == "close":
            fd = _arg(args, kwargs, "fd", 0)
            if isinstance(fd, int):
                self._passthrough_fds.pop(fd, None)
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


def _result_fd_and_owner(result) -> tuple[int | None, object | None]:
    if isinstance(result, int) and result >= 0:
        return result, None

    fileno = getattr(result, "fileno", None)
    if fileno is None:
        return None, None

    try:
        fd = fileno()
    except Exception:
        return None, None

    if isinstance(fd, int) and fd >= 0:
        return fd, result
    return None, None


def _owner_ref(owner, provenance: FdProvenance, fd: int):
    if owner is None:
        return None

    try:
        return weakref.ref(owner, lambda _ref: provenance._passthrough_fds.pop(fd, None))
    except TypeError:
        return None
