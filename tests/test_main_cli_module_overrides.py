import os
import tempfile

from retracesoftware.__main__ import _cli_module_overrides


def test_cli_module_overrides_does_not_seed_tempfile_rng(monkeypatch):
    monkeypatch.delenv("RETRACE_MODULES_PATH", raising=False)
    monkeypatch.setattr(tempfile, "_name_sequence", None)

    with _cli_module_overrides():
        assert "RETRACE_MODULES_PATH" not in os.environ

    assert tempfile._name_sequence is None
