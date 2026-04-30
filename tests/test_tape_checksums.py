from pathlib import Path

from retracesoftware.tape import checksum


def test_checksum_ignores_agent_and_design_docs(tmp_path: Path):
    package_dir = tmp_path / "retracesoftware"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package_dir / "AGENTS.md").write_text("local instructions\n", encoding="utf-8")
    (package_dir / "DESIGN.md").write_text("design notes\n", encoding="utf-8")
    (package_dir / "STREAM_DESIGN.md").write_text("stream notes\n", encoding="utf-8")

    result = checksum(package_dir)

    assert "__init__.py" in result
    assert "AGENTS.md" not in result
    assert "DESIGN.md" not in result
    assert "STREAM_DESIGN.md" not in result
