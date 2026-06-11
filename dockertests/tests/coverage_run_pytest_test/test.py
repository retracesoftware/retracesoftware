from pathlib import Path


def test_coverage_run_pytest_exercises_user_code(tmp_path):
    value_file = Path(tmp_path) / "value.txt"
    value_file.write_text("42", encoding="utf-8")

    value = int(value_file.read_text(encoding="utf-8"))

    assert value == 42
