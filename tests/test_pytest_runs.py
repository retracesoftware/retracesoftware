from __future__ import annotations

import json

import pytest

from retracesoftware.pytest_runs import (
    NoRunsFoundError,
    build_failed_test_manifest,
    latest_run,
    list_runs,
    read_manifest,
    resolve_recording_arg,
    write_manifest,
)


def _manifest(run_id: str, created_at: str, recording_path: str = "recording.bin"):
    return build_failed_test_manifest(
        run_id=run_id,
        created_at=created_at,
        recording_path=recording_path,
        node_id=f"tests/test_example.py::{run_id}",
        test_file="tests/test_example.py",
        test_function=run_id,
        exception_type="AssertionError",
        exception_message=f"{run_id} failed",
    )


def test_write_and_read_failed_test_manifest(tmp_path):
    runs_dir = tmp_path / ".retrace" / "runs"
    manifest_path = write_manifest(
        _manifest("run-a", "2026-06-05T10:00:00Z"),
        runs_dir=runs_dir,
    )

    assert manifest_path == runs_dir / "run-a" / "manifest.json"
    manifest = read_manifest(manifest_path)
    assert manifest["schema_version"] == 1
    assert manifest["run_id"] == "run-a"
    assert manifest["pytest"]["node_id"] == "tests/test_example.py::run-a"
    assert manifest["recording"] == {
        "available": False,
        "capture_method": "placeholder",
        "failure_reason": None,
        "placeholder": True,
    }
    assert manifest["safety"]["env_values_included"] is False


def test_list_runs_newest_first(tmp_path):
    runs_dir = tmp_path / ".retrace" / "runs"
    write_manifest(_manifest("older", "2026-06-05T09:00:00Z"), runs_dir=runs_dir)
    write_manifest(_manifest("newer", "2026-06-05T11:00:00Z"), runs_dir=runs_dir)

    manifests = list_runs(tmp_path)

    assert [manifest["run_id"] for manifest in manifests] == ["newer", "older"]


def test_latest_run_resolves_from_nested_subdirectory(tmp_path):
    runs_dir = tmp_path / ".retrace" / "runs"
    nested = tmp_path / "pkg" / "tests"
    nested.mkdir(parents=True)
    write_manifest(
        _manifest("latest", "2026-06-05T11:00:00Z", str(tmp_path / "latest.bin")),
        runs_dir=runs_dir,
    )

    manifest = latest_run(nested)
    recording = resolve_recording_arg(latest=True, start_path=nested)

    assert manifest["run_id"] == "latest"
    assert recording == tmp_path / "latest.bin"


def test_latest_run_has_helpful_error_when_no_runs_exist(tmp_path):
    with pytest.raises(NoRunsFoundError, match="No Retrace failed-test runs found"):
        latest_run(tmp_path)


def test_manifest_does_not_write_environment_variable_values(tmp_path, monkeypatch):
    monkeypatch.setenv("RETRACE_SECRET_TOKEN", "do-not-write-this")
    manifest_path = write_manifest(
        build_failed_test_manifest(
            run_id="safe-env",
            created_at="2026-06-05T12:00:00Z",
            env_var_names=["RETRACE_SECRET_TOKEN"],
        ),
        runs_dir=tmp_path / ".retrace" / "runs",
    )

    raw_manifest = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(raw_manifest)

    assert "RETRACE_SECRET_TOKEN" in manifest["safety"]["env_var_names"]
    assert "do-not-write-this" not in raw_manifest
    assert manifest["safety"]["env_values_included"] is False
