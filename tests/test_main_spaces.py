from argparse import Namespace
from contextlib import contextmanager
from types import SimpleNamespace

import retracesoftware.__main__ as main


class _Space:
    def __init__(self):
        self.calls = []

    def run(self, function, *args, **kwargs):
        self.calls.append((function, args, kwargs))
        return function(*args, **kwargs)


class _System:
    @contextmanager
    def enable(self):
        yield


def test_run_target_runs_application_inside_internal_space(monkeypatch):
    internal_space = _Space()
    runner = main.Runner(
        argv=["target.py"],
        system=_System(),
        options=Namespace(trace_shutdown=False),
        internal_space=internal_space,
    )

    monkeypatch.setattr(main, "run_python_command", lambda argv: ("ran", list(argv)))

    assert main._run_target(runner) == ("ran", ["target.py"])
    assert len(internal_space.calls) == 1
    assert internal_space.calls[0][0] is main._run_enabled_target
    assert internal_space.calls[0][1] == (runner,)


def test_create_runner_runs_in_default_disabled_space(monkeypatch):
    disabled_space = _Space()
    retrace = SimpleNamespace(
        disabled_space=disabled_space,
        run_disabled=lambda _function: (_ for _ in ()).throw(
            AssertionError("run_disabled fallback should not be used")
        ),
    )

    monkeypatch.setattr(main, "retrace", retrace)
    monkeypatch.setattr(main, "create_runner", lambda: "runner")

    assert main._create_runner_disabled() == "runner"
    assert len(disabled_space.calls) == 1
    assert disabled_space.calls[0][0] is main.create_runner
