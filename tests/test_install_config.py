from retracesoftware.install.config import config_to_argv, load_retrace_config


def test_config_to_argv_emits_format():
    argv = config_to_argv({"record": {"format": "unframed_binary"}})
    assert argv == ["--format", "unframed_binary"]


def test_load_retrace_config_applies_format_env_override(monkeypatch):
    monkeypatch.setenv("RETRACE_FORMAT", "unframed_binary")
    config = load_retrace_config("release")

    assert config["record"]["format"] == "unframed_binary"
