import inspect
from contextlib import contextmanager
from types import SimpleNamespace

from retracesoftware.install import edgecases


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
