from retracesoftware.install.config import config_to_argv, load_retrace_config


def test_config_to_argv_emits_gc_collect_multiplier():
    argv = config_to_argv({"record": {"gc_collect_multiplier": 123}})
    assert argv == ["--gc_collect_multiplier", "123"]


def test_config_to_argv_emits_format():
    argv = config_to_argv({"record": {"format": "unframed_binary"}})
    assert argv == ["--format", "unframed_binary"]


def test_load_retrace_config_applies_gc_collect_multiplier_env_override(monkeypatch):
    monkeypatch.setenv("RETRACE_GC_COLLECT_MULTIPLIER", "456")
    config = load_retrace_config("release")

    assert config["record"]["gc_collect_multiplier"] == "456"


def test_load_retrace_config_applies_format_env_override(monkeypatch):
    monkeypatch.setenv("RETRACE_FORMAT", "unframed_binary")
    config = load_retrace_config("release")

    assert config["record"]["format"] == "unframed_binary"
