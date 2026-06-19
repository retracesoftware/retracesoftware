from retracesoftware.modules import ModuleConfigResolver


def test_builtin_tempfile_config_disables_tempdir_probe(monkeypatch):
    monkeypatch.delenv("RETRACE_MODULES_PATH", raising=False)

    resolver = ModuleConfigResolver()

    assert resolver["tempfile"]["disable"] == ["_get_default_tempdir"]
