from argparse import Namespace
from types import SimpleNamespace

import retracesoftware.__main__ as main


class _Space:
    def __init__(self):
        self.calls = []

    def run(self, function, *args, **kwargs):
        self.calls.append((function, args, kwargs))
        return function(*args, **kwargs)


class _System:
    def __init__(self):
        self.calls = []

    def run(self, function, *args, **kwargs):
        self.calls.append((function, args, kwargs))
        return function(*args, **kwargs)


def test_run_target_uses_system_run(monkeypatch):
    internal_space = _Space()
    system = _System()
    runner = main.Runner(
        argv=["target.py"],
        system=system,
        options=Namespace(trace_shutdown=False),
        internal_space=internal_space,
    )

    monkeypatch.setattr(main, "run_python_command", lambda argv: ("ran", list(argv)))

    assert main._run_target(runner) == ("ran", ["target.py"])
    assert internal_space.calls == []
    assert len(system.calls) == 1
    assert system.calls[0][0] is main.run_python_command
    assert system.calls[0][1] == (["target.py"],)


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
