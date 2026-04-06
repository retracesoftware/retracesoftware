from retracesoftware.install.config import config_to_argv, load_retrace_config


def test_config_to_argv_emits_gc_collect_multiplier():
    argv = config_to_argv({"record": {"gc_collect_multiplier": 123}})
    assert argv == ["--gc_collect_multiplier", "123"]


def test_load_retrace_config_applies_gc_collect_multiplier_env_override(monkeypatch):
    monkeypatch.setenv("RETRACE_GC_COLLECT_MULTIPLIER", "456")
    config = load_retrace_config("release")

    assert config["record"]["gc_collect_multiplier"] == "456"
