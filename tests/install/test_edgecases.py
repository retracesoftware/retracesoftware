import _thread
import inspect
from contextlib import contextmanager
from types import SimpleNamespace

from retracesoftware.install import edgecases


class _TrylockWriter:
    def __init__(self):
        self.results = []

    def result(self, value):
        self.results.append(value)


class _TrylockReader:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def result(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self._result


class _TrylockSystem:
    def __init__(self, mode, *, result=None):
        self._mode = mode
        self.writer = _TrylockWriter()
        self.reader = _TrylockReader(result)
        self.factories = []
        self.calls = []
        self.location = "internal"

    @property
    def retrace_mode(self):
        raise AssertionError("try-lock wrapper should not inspect mode")

    def record_replay_operation(self, recorder, replayer):
        if self._mode == "record":
            self.factories.append("recorder")
            operation = recorder(self.writer)
        else:
            self.factories.append("replayer")
            operation = replayer(self.reader)

        def wrapped(*args, **kwargs):
            self.calls.append((args, kwargs))
            return operation(*args, **kwargs)

        return wrapped


class _Semaphore:
    def __init__(self, count):
        self.count = count


def _try_acquire(target_calls):
    def target(self, blocking=True, timeout=None):
        target_calls.append((blocking, timeout))
        if self.count > 0:
            self.count -= 1
            return True
        return False

    return target


def test_pthread_sigmask_converts_set_mask_to_tuple_before_target_call():
    calls = []

    def target(how, mask):
        calls.append((how, mask))
        return {1, 2}

    wrapped = edgecases.pthread_sigmask(target)

    assert sorted(wrapped("setmask", {2, 15})) == [1, 2]
    assert calls[0][0] == "setmask"
    assert sorted(calls[0][1]) == [2, 15]


def test_semaphore_trylock_record_writes_result_without_mode_branch():
    target_calls = []
    system = _TrylockSystem("record")
    semaphore = _Semaphore(1)
    wrapped = edgecases.trylock(
        _try_acquire(target_calls),
        system=system,
    )

    assert wrapped(semaphore, timeout=0) is True

    assert semaphore.count == 0
    assert target_calls == [(True, 0)]
    assert system.factories == ["recorder"]
    assert system.writer.results == [True]


def test_semaphore_trylock_replay_syncs_state_when_recorded_true():
    target_calls = []
    system = _TrylockSystem("replay", result=True)
    semaphore = _Semaphore(1)
    wrapped = edgecases.trylock(
        _try_acquire(target_calls),
        system=system,
    )

    assert wrapped(semaphore, timeout=0) is True

    assert semaphore.count == 0
    assert target_calls == [(True, 0)]
    assert system.factories == ["replayer"]
    assert system.reader.calls == [((semaphore,), {"timeout": 0})]


def test_semaphore_trylock_replay_leaves_state_when_recorded_false():
    target_calls = []
    system = _TrylockSystem("replay", result=False)
    semaphore = _Semaphore(1)
    wrapped = edgecases.trylock(
        _try_acquire(target_calls),
        system=system,
    )

    assert wrapped(semaphore, timeout=0) is False

    assert semaphore.count == 1
    assert target_calls == []
    assert system.factories == ["replayer"]
    assert system.reader.calls == [((semaphore,), {"timeout": 0})]


def test_native_lock_trylock_record_writes_result_without_signature():
    system = _TrylockSystem("record")
    lock = _thread.allocate_lock()
    wrapped = edgecases.trylock(_thread.LockType.acquire, system=system)

    assert wrapped(lock, False) is True

    assert lock.locked() is True
    assert system.factories == ["recorder"]
    assert system.writer.results == [True]

    lock.release()


def test_native_lock_trylock_replay_syncs_state_when_recorded_true():
    system = _TrylockSystem("replay", result=True)
    lock = _thread.allocate_lock()
    wrapped = edgecases.trylock(_thread.LockType.acquire, system=system)

    assert wrapped(lock, False) is True

    assert lock.locked() is True
    assert system.factories == ["replayer"]
    assert system.reader.calls == [((lock, False), {})]

    lock.release()


def test_native_lock_trylock_replay_leaves_state_when_recorded_false():
    system = _TrylockSystem("replay", result=False)
    lock = _thread.allocate_lock()
    wrapped = edgecases.trylock(_thread.LockType.acquire, system=system)

    assert wrapped(lock, False) is False

    assert lock.locked() is False
    assert system.factories == ["replayer"]
    assert system.reader.calls == [((lock, False), {})]


def test_native_rlock_trylock_record_writes_result_without_signature():
    system = _TrylockSystem("record")
    lock = _thread.RLock()
    wrapped = edgecases.trylock(_thread.RLock.acquire, system=system)

    assert wrapped(lock, False) is True

    assert lock._is_owned() is True
    assert system.factories == ["recorder"]
    assert system.writer.results == [True]

    lock.release()


def test_native_rlock_trylock_replay_syncs_state_when_recorded_true():
    system = _TrylockSystem("replay", result=True)
    lock = _thread.RLock()
    wrapped = edgecases.trylock(_thread.RLock.acquire, system=system)

    assert wrapped(lock, False) is True

    assert lock._is_owned() is True
    assert system.factories == ["replayer"]
    assert system.reader.calls == [((lock, False), {})]

    lock.release()


def test_native_rlock_trylock_replay_leaves_state_when_recorded_false():
    system = _TrylockSystem("replay", result=False)
    lock = _thread.RLock()
    wrapped = edgecases.trylock(_thread.RLock.acquire, system=system)

    assert wrapped(lock, False) is False

    assert lock._is_owned() is False
    assert system.factories == ["replayer"]
    assert system.reader.calls == [((lock, False), {})]


def test_trylock_external_space_uses_raw_target_without_recording_result():
    target_calls = []
    system = _TrylockSystem("record")
    system.location = "external"
    semaphore = _Semaphore(1)
    wrapped = edgecases.trylock(
        _try_acquire(target_calls),
        system=system,
    )

    assert wrapped(semaphore, timeout=0) is True

    assert semaphore.count == 0
    assert target_calls == [(True, 0)]
    assert system.writer.results == []


def test_asyncio_write_to_self_calls_target_from_same_site_on_record_and_replay():
    observations = []

    def target(self):
        frame = inspect.currentframe()
        observations.append("target")
        return frame.f_back.f_code, frame.f_back.f_lineno

    class FakeSystem:
        retrace_mode = "record"

        @property
        def retrace_mode(self):
            observations.append("mode")
            return self._retrace_mode

        @retrace_mode.setter
        def retrace_mode(self, value):
            self._retrace_mode = value

        def handoff_replay_thread_schedule_to(self, thread_id):
            handoffs.append(thread_id)

        def disable_for(self, raw, *, unwrap_args):
            return consumed.append

    @contextmanager
    def defer_schedule():
        yield

    consumed = []
    handoffs = []
    system = FakeSystem()
    system.retrace_mode = "record"
    system.defer_replay_thread_schedule = defer_schedule
    loop = SimpleNamespace(_thread_id="loop-thread")
    wrapped = edgecases.asyncio_write_to_self(target, system=system)

    record_site = wrapped(loop)
    assert observations == ["target", "mode"]
    observations.clear()

    system.retrace_mode = "replay"
    replay_site = wrapped(loop)

    assert replay_site == record_site
    assert observations == ["target", "mode"]
    assert handoffs == ["loop-thread"]
    assert consumed == [loop]
