import time

from tests.runner import retrace_test


_CALLS = []


class _Counter:
    def ping(self):
        _CALLS.append("ping")
        return len(_CALLS)


@retrace_test
def test_time():
    return time.time()


@retrace_test(matrix="core")
def test_time_core_matrix():
    return time.time()


@retrace_test(patch=[_Counter])
def test_patch_types():
    return _Counter().ping()
