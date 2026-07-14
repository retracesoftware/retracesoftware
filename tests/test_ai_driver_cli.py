from retracesoftware.ai_driver import _parser


def test_ai_driver_defaults_to_60_tool_calls(monkeypatch) -> None:
    monkeypatch.delenv("RETRACE_AI_MAX_TOOL_CALLS", raising=False)

    args = _parser().parse_args([])

    assert args.max_tool_calls == 60


def test_ai_driver_honors_tool_call_environment_override(monkeypatch) -> None:
    monkeypatch.setenv("RETRACE_AI_MAX_TOOL_CALLS", "17")

    args = _parser().parse_args([])

    assert args.max_tool_calls == 17
