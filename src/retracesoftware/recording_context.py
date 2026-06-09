"""Generic recording resolution and agent context helpers."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
from typing import Any, Mapping


LATEST_RECORDING_POINTER = Path(".retrace") / "latest-recording.json"


class RecordingResolutionError(ValueError):
    """Raised when a recording argument cannot be resolved."""


def _start_path(start_path: str | os.PathLike[str] | None = None) -> Path:
    return Path.cwd() if start_path is None else Path(start_path)


def find_latest_recording_pointer(
    start_path: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Find a generic latest-recording pointer by walking upward."""
    current = _start_path(start_path).resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / LATEST_RECORDING_POINTER
        if candidate.is_file():
            return candidate
    return None


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RecordingResolutionError(f"could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RecordingResolutionError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RecordingResolutionError(f"{path} must contain a JSON object")
    return value


def _resolve_path(raw_path: object, *, base_dir: Path | None = None) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute() and base_dir is not None:
        path = base_dir / path
    return path


def read_latest_recording(
    start_path: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Read the nearest generic latest-recording pointer."""
    pointer = find_latest_recording_pointer(start_path)
    if pointer is None:
        raise RecordingResolutionError(
            f"no latest recording pointer found; create {LATEST_RECORDING_POINTER} "
            "or pass --recording"
        )
    data = _load_json(pointer)
    base_dir = pointer.parent.parent
    recording_path = _resolve_path(data.get("recording_path"), base_dir=base_dir)
    if recording_path is None:
        raise RecordingResolutionError(f"{pointer} does not contain recording_path")
    manifest_path = _resolve_path(data.get("manifest_path"), base_dir=base_dir)
    return {
        "pointer_path": pointer,
        "recording_path": recording_path,
        "manifest_path": manifest_path,
        "metadata": data,
    }


def resolve_recording(
    *,
    recording: str | os.PathLike[str] | None = None,
    latest: bool = False,
    env: Mapping[str, str] | None = None,
    start_path: str | os.PathLike[str] | None = None,
) -> Path:
    """Resolve a recording path from explicit args, latest pointer, or env."""
    if recording is not None and latest:
        raise RecordingResolutionError("pass either --recording or --latest, not both")
    if recording is not None:
        return Path(recording).expanduser()
    if latest:
        return Path(read_latest_recording(start_path)["recording_path"])
    env = os.environ if env is None else env
    env_recording = env.get("RETRACE_RECORDING")
    if env_recording:
        return Path(env_recording).expanduser()
    raise RecordingResolutionError("recording path is required; pass --recording or --latest")


def resolve_manifest(
    *,
    manifest: str | os.PathLike[str] | None = None,
    latest: bool = False,
    start_path: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Resolve an optional manifest path."""
    if manifest is not None:
        return Path(manifest).expanduser()
    if latest:
        return read_latest_recording(start_path).get("manifest_path")
    return None


def _file_info(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {"path": "", "exists": False, "size_bytes": None}
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists and path.is_file() else None,
    }


def _read_manifest(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    return _load_json(path)


def build_agent_context(recording_path: Path, manifest_path: Path | None = None) -> dict[str, Any]:
    """Build a stable, evidence-only context packet for an agent."""
    manifest = _read_manifest(manifest_path)
    failure = manifest.get("failure", {}) if isinstance(manifest, dict) else {}
    if not isinstance(failure, dict):
        failure = {}

    inspect_command = "retrace inspect --recording " + shlex.quote(str(recording_path))
    mcp_command = "retrace mcp --recording " + shlex.quote(str(recording_path))

    return {
        "recording": _file_info(recording_path),
        "manifest": _file_info(manifest_path),
        "failure": {
            "exception_type": failure.get("exception_type", ""),
            "exception_message": failure.get("exception_message", ""),
            "traceback_summary": failure.get("traceback_summary", ""),
        },
        "inspection": {
            "final_exception_available": bool(failure.get("exception_type")),
            "frames_available": False,
            "locals_available": False,
            "reason": "agent-context reports file and manifest facts only; run inspect for replay state",
        },
        "commands": {
            "inspect": inspect_command,
            "mcp": mcp_command,
        },
        "safety": {
            "local_file": True,
            "may_contain_runtime_data": True,
            "may_contain_secrets": True,
        },
    }


def render_agent_context_text(context: dict[str, Any]) -> str:
    """Render an agent context packet as plain text."""
    recording = context["recording"]
    manifest = context["manifest"]
    inspection = context["inspection"]
    commands = context["commands"]
    safety = context["safety"]

    lines = [
        "Retrace recording context",
        "",
        "Recording:",
        f"  path: {recording['path']}",
        f"  exists: {'yes' if recording['exists'] else 'no'}",
        f"  size: {recording['size_bytes'] if recording['size_bytes'] is not None else 'unavailable'} bytes",
        "",
        "Manifest:",
        f"  path: {manifest['path'] or 'unavailable'}",
        f"  exists: {'yes' if manifest['exists'] else 'no'}",
        "",
        "Inspection:",
        f"  final exception: {'available' if inspection['final_exception_available'] else 'not available'}",
        f"  frames: {'available' if inspection['frames_available'] else 'not available'}",
        f"  locals: {'available' if inspection['locals_available'] else 'not available'}",
        f"  note: {inspection['reason']}",
        "",
        "Useful commands:",
        f"  {commands['inspect']}",
        f"  {commands['mcp']}",
        "",
        "Safety:",
    ]
    if safety["may_contain_runtime_data"]:
        lines.append("  Recordings may contain runtime data, secrets, API responses, or database-derived values.")
    lines.append("  Share or upload recordings only when you intend to.")
    lines.append("")
    return "\n".join(lines)
