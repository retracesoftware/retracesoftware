"""Failed-test run manifest storage for pytest-oriented Retrace workflows."""

from __future__ import annotations

import json
import platform
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
RUNS_ROOT = Path(".retrace") / "runs"
PLACEHOLDER_RECORDING_TEXT = (
    "Retrace pytest placeholder recording.\n"
    "Recording capture was unavailable for this pytest run.\n"
)
LEGACY_PLACEHOLDER_RECORDING_TEXT = (
    "Retrace pytest placeholder recording.\n"
    "Recording capture is not wired into pytest --retrace yet.\n"
)


class RunManifestError(ValueError):
    """Raised when a failed-test run manifest is invalid."""


class NoRunsFoundError(FileNotFoundError):
    """Raised when no failed-test run manifests can be found."""


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_created_at(value: object) -> datetime:
    if not isinstance(value, str):
        return datetime.min.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _start_dir(start_path: str | Path | None = None) -> Path:
    path = Path.cwd() if start_path is None else Path(start_path)
    if path.exists() and path.is_file():
        return path.parent
    return path


def _repo_root(start_path: str | Path | None = None) -> Path:
    current = _start_dir(start_path).resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def create_run_id() -> str:
    """Create a sortable run id for one failed-test recording."""

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def get_default_runs_dir(start_path: str | Path | None = None) -> Path:
    """Return the default `.retrace/runs` directory for a project."""

    return _repo_root(start_path) / RUNS_ROOT


def find_runs_dir(start_path: str | Path | None = None) -> Path | None:
    """Find the nearest existing `.retrace/runs` directory by walking upward."""

    current = _start_dir(start_path).resolve()
    for candidate in (current, *current.parents):
        runs_dir = candidate / RUNS_ROOT
        if runs_dir.is_dir():
            return runs_dir
    return None


def build_failed_test_manifest(
    *,
    run_id: str | None = None,
    created_at: str | None = None,
    recording_path: str | Path | None = None,
    manifest_path: str | Path | None = None,
    failure_path: str | Path | None = None,
    node_id: str = "",
    test_file: str = "",
    test_line: int | None = None,
    test_function: str = "",
    pytest_version: str = "",
    active_plugins: list[str] | None = None,
    randomly_detected: bool = False,
    randomly_seed: str | int | None = None,
    env_detected: bool = False,
    sugar_detected: bool = False,
    teamcity_messages_detected: bool = False,
    exception_type: str = "",
    exception_message: str = "",
    traceback_summary: str = "",
    recording_placeholder: bool = True,
    recording_capture_method: str = "placeholder",
    recording_available: bool | None = None,
    recording_failure_reason: str | None = None,
    cwd: str | Path | None = None,
    repo_root: str | Path | None = None,
    coverage_detected: bool = False,
    teamcity_detected: bool = False,
    ci_detected: bool = False,
    env_var_names: list[str] | None = None,
) -> dict[str, Any]:
    """Build a versioned manifest without capturing environment values."""

    safe_env_names = sorted(set(env_var_names or []))
    real_recording_available = not recording_placeholder if recording_available is None else recording_available
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or create_run_id(),
        "created_at": created_at or _utc_now(),
        "recording_path": str(recording_path) if recording_path is not None else "",
        "manifest_path": str(manifest_path) if manifest_path is not None else "",
        "failure_path": str(failure_path) if failure_path is not None else "",
        "recording": {
            "placeholder": recording_placeholder,
            "capture_method": recording_capture_method,
            "available": real_recording_available,
            "failure_reason": recording_failure_reason,
        },
        "pytest": {
            "node_id": node_id,
            "test_file": test_file,
            "test_line": test_line,
            "test_function": test_function,
            "pytest_version": pytest_version,
            "active_plugins": list(active_plugins or []),
            "randomly_detected": randomly_detected,
            "randomly_seed": randomly_seed,
            "env_detected": env_detected,
            "sugar_detected": sugar_detected,
            "teamcity_messages_detected": teamcity_messages_detected,
            "coverage_detected": coverage_detected,
        },
        "failure": {
            "exception_type": exception_type,
            "exception_message": exception_message,
            "traceback_summary": traceback_summary,
        },
        "environment": {
            "python_version": platform.python_version(),
            "platform": platform.platform(),
            "cwd": str(cwd if cwd is not None else Path.cwd()),
            "repo_root": str(repo_root if repo_root is not None else _repo_root(cwd)),
            "coverage_detected": coverage_detected,
            "teamcity_detected": teamcity_detected,
            "ci_detected": ci_detected,
        },
        "safety": {
            "env_values_included": False,
            "env_var_names": safe_env_names,
        },
    }


def _validate_manifest(manifest: dict[str, Any], *, source: Path | None = None) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        label = f" in {source}" if source is not None else ""
        raise RunManifestError(f"unsupported failed-test manifest schema{label}")
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        label = f" in {source}" if source is not None else ""
        raise RunManifestError(f"failed-test manifest is missing run_id{label}")
    safety = manifest.get("safety")
    if not isinstance(safety, dict) or safety.get("env_values_included") is not False:
        label = f" in {source}" if source is not None else ""
        raise RunManifestError(f"failed-test manifest must not include environment values{label}")


def write_manifest(manifest: dict[str, Any], *, runs_dir: str | Path | None = None) -> Path:
    """Write a failed-test manifest under `.retrace/runs/<run-id>/manifest.json`."""

    _validate_manifest(manifest)
    target_runs_dir = Path(runs_dir) if runs_dir is not None else get_default_runs_dir()
    run_dir = target_runs_dir / str(manifest["run_id"])
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest_to_write = dict(manifest)
    recording_path = manifest_to_write.get("recording_path") or str(run_dir / "recording.bin")
    failure_path = manifest_to_write.get("failure_path") or str(run_dir / "failure.txt")
    manifest_path = run_dir / "manifest.json"

    manifest_to_write["recording_path"] = str(recording_path)
    manifest_to_write["failure_path"] = str(failure_path)
    manifest_to_write["manifest_path"] = str(manifest_path)
    manifest_path.write_text(json.dumps(manifest_to_write, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


def read_manifest(path: str | Path) -> dict[str, Any]:
    """Read and validate a failed-test run manifest."""

    manifest_path = Path(path)
    with manifest_path.open(encoding="utf-8") as handle:
        manifest = json.load(handle)
    if not isinstance(manifest, dict):
        raise RunManifestError(f"failed-test manifest must be a JSON object in {manifest_path}")
    _validate_manifest(manifest, source=manifest_path)
    return manifest


def list_runs(start_path: str | Path | None = None) -> list[dict[str, Any]]:
    """List valid failed-test runs newest-first."""

    runs_dir = find_runs_dir(start_path)
    if runs_dir is None:
        return []

    manifests: list[tuple[datetime, float, dict[str, Any]]] = []
    for manifest_path in runs_dir.glob("*/manifest.json"):
        try:
            manifest = read_manifest(manifest_path)
        except (OSError, json.JSONDecodeError, RunManifestError):
            continue
        manifests.append((
            _parse_created_at(manifest.get("created_at")),
            manifest_path.stat().st_mtime,
            manifest,
        ))

    manifests.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [manifest for _, _, manifest in manifests]


def latest_run(start_path: str | Path | None = None) -> dict[str, Any]:
    """Return the newest valid failed-test run manifest."""

    manifests = list_runs(start_path)
    if manifests:
        return manifests[0]
    searched = find_runs_dir(start_path) or get_default_runs_dir(start_path)
    raise NoRunsFoundError(f"No Retrace failed-test runs found under {searched}")


def resolve_recording_arg(
    recording: str | Path | None = None,
    *,
    latest: bool = False,
    start_path: str | Path | None = None,
) -> Path:
    """Resolve an explicit recording path or the latest failed-test run recording."""

    if latest and recording:
        raise ValueError("pass either a recording path or --latest, not both")
    if latest:
        manifest = latest_run(start_path)
        recording_path = manifest.get("recording_path")
        if not isinstance(recording_path, str) or not recording_path:
            raise RunManifestError(f"latest failed-test run {manifest.get('run_id')} has no recording_path")
        return Path(recording_path)
    if recording:
        return Path(recording)
    raise ValueError("recording path is required unless --latest is used")


def is_placeholder_recording(path: str | Path) -> bool:
    """Return whether a recording file is the pytest placeholder artifact."""

    recording_path = Path(path)
    try:
        prefix = recording_path.read_text(encoding="utf-8", errors="replace")[: len(LEGACY_PLACEHOLDER_RECORDING_TEXT)]
    except OSError:
        return False
    return prefix.startswith(PLACEHOLDER_RECORDING_TEXT) or prefix.startswith(LEGACY_PLACEHOLDER_RECORDING_TEXT)
