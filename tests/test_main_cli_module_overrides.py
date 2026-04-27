import os
from pathlib import Path
import tempfile

from retracesoftware.__main__ import _cli_module_overrides


def test_cli_module_overrides_does_not_seed_tempfile_rng(monkeypatch):
    monkeypatch.delenv("RETRACE_MODULES_PATH", raising=False)
    monkeypatch.setattr(tempfile, "_name_sequence", None)

    with _cli_module_overrides():
        modules_dir = Path(os.environ["RETRACE_MODULES_PATH"])
        assert (modules_dir / "_io.toml").is_file()

    assert tempfile._name_sequence is None
    assert not modules_dir.exists()
