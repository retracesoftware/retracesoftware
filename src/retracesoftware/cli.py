"""User-facing Retrace command wrappers."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Sequence

from retracesoftware.agent_inspect import (
    inspect_external_call,
    inspect_external_calls,
    inspect_frame,
    inspect_provenance,
    inspect_recording,
    inspect_variable,
    render_external_call_markdown,
    render_external_calls_markdown,
    render_frame_markdown,
    render_json,
    render_markdown,
    render_provenance_markdown,
    render_variable_markdown,
)
from retracesoftware.pytest_runs import (
    NoRunsFoundError,
    RunManifestError,
    find_runs_dir,
    get_default_runs_dir,
    is_placeholder_recording,
    latest_run,
    list_runs,
    resolve_recording_arg,
)


DEFAULT_PYTEST_RECORDING = "recordings/pytest.retrace"
DEFAULT_PYTEST_FORMAT = "binary"


def _resolve_recording(args: argparse.Namespace, *, command: str) -> str | None:
    try:
        return str(resolve_recording_arg(
            getattr(args, "recording", None),
            latest=getattr(args, "latest", False),
        ))
    except (NoRunsFoundError, RunManifestError, ValueError) as exc:
        print(f"{command} failed: {exc}", file=sys.stderr)
        return None


def _latest_manifest(*, command: str) -> dict | None:
    try:
        return latest_run()
    except NoRunsFoundError as exc:
        print(f"{command} failed: {exc}. Run pytest with --retrace first.", file=sys.stderr)
        return None


def _artifact_path(manifest: dict, key: str) -> Path | None:
    raw_path = manifest.get(key)
    return Path(raw_path) if isinstance(raw_path, str) and raw_path else None


def _recording_status(manifest: dict, recording_path: Path | None) -> dict:
    recording = manifest.get("recording") if isinstance(manifest.get("recording"), dict) else {}
    recording_exists = recording_path.exists() if recording_path is not None else False
    placeholder = recording.get("placeholder")
    if not isinstance(placeholder, bool):
        placeholder = is_placeholder_recording(recording_path) if recording_exists else False
    available = recording.get("available")
    if not isinstance(available, bool):
        available = recording_exists and not placeholder
    return {
        "exists": recording_exists,
        "available": available,
        "placeholder": placeholder,
        "capture_method": recording.get("capture_method", ""),
        "failure_reason": recording.get("failure_reason"),
    }


def _latest_recording_for_command(*, command: str) -> tuple[dict, Path] | None:
    manifest = _latest_manifest(command=command)
    if manifest is None:
        return None
    recording_path = _artifact_path(manifest, "recording_path")
    status = _recording_status(manifest, recording_path)
    if recording_path is None:
        print(f"{command} failed: latest run has no recording path.", file=sys.stderr)
        return None
    if not status["available"]:
        reason = status["failure_reason"] or "recording was not captured for the latest run"
        print(f"{command} failed: latest recording is unavailable. {reason}", file=sys.stderr)
        return None
    if not recording_path.exists():
        print(f"{command} failed: latest recording is missing: {recording_path}", file=sys.stderr)
        return None
    if status["placeholder"]:
        print(f"{command} failed: latest recording is a placeholder, not a replayable recording.", file=sys.stderr)
        return None
    return manifest, recording_path


def _agent_context(manifest: dict) -> dict:
    recording_path = _artifact_path(manifest, "recording_path")
    manifest_path = _artifact_path(manifest, "manifest_path")
    failure_path = _artifact_path(manifest, "failure_path")
    pytest_info = manifest.get("pytest") if isinstance(manifest.get("pytest"), dict) else {}
    failure = manifest.get("failure") if isinstance(manifest.get("failure"), dict) else {}
    recording = manifest.get("recording") if isinstance(manifest.get("recording"), dict) else {}
    recording_status = _recording_status(manifest, recording_path)
    return {
        "title": "Retrace failed-test context",
        "run_id": manifest.get("run_id", ""),
        "test": {
            "node_id": pytest_info.get("node_id", ""),
            "file": pytest_info.get("test_file", ""),
            "line": pytest_info.get("test_line"),
            "function": pytest_info.get("test_function", ""),
        },
        "failure": {
            "exception_type": failure.get("exception_type", ""),
            "exception_message": failure.get("exception_message", ""),
            "traceback_summary": failure.get("traceback_summary", ""),
        },
        "artifacts": {
            "recording": str(recording_path) if recording_path is not None else "",
            "manifest": str(manifest_path) if manifest_path is not None else "",
            "failure": str(failure_path) if failure_path is not None else "",
        },
        "evidence": {
            "recording_exists": recording_status["exists"],
            "recording_available": recording_status["available"],
            "recording_placeholder": recording_status["placeholder"],
            "recording_capture_method": recording_status["capture_method"],
            "recording_capture_scope": recording.get("capture_scope", ""),
            "recording_failure_selection": recording.get("failure_selection", ""),
            "recording_failure_reason": recording_status["failure_reason"],
            "inspect_available": "unknown",
            "external_calls_available": "unknown",
        },
        "useful_commands": [
            "retrace inspect --latest",
            "retrace runs",
            "retrace mcp --latest",
            "retrace vscode --latest",
        ],
        "safety": {
            "recording_is_local": True,
            "recording_may_contain_runtime_data": True,
            "manifest_env_values_excluded_by_default": True,
        },
    }


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _render_agent_context(context: dict) -> str:
    failure = context["failure"]
    failure_label = failure["exception_type"]
    if failure["exception_message"]:
        failure_label = f"{failure_label}: {failure['exception_message']}"
    lines = [
        "Retrace failed-test context",
        "",
        "Test:",
        context["test"]["node_id"] or "<unknown test>",
    ]
    if context["test"]["file"]:
        source = context["test"]["file"]
        if context["test"]["line"] is not None:
            source = f"{source}:{context['test']['line']}"
        lines.extend(["", "Source:", source])
    lines.extend([
        "",
        "Failure:",
        failure_label,
    ])
    if failure["traceback_summary"]:
        lines.extend(["", "Traceback summary:", failure["traceback_summary"]])
    lines.extend([
        "",
        "Artifacts:",
        f"recording: {context['artifacts']['recording']}",
        f"manifest: {context['artifacts']['manifest']}",
        f"failure: {context['artifacts']['failure']}",
        "",
        "Evidence:",
        f"recording_exists: {_yes_no(context['evidence']['recording_exists'])}",
        f"recording_available: {_yes_no(context['evidence']['recording_available'])}",
        f"recording_placeholder: {_yes_no(context['evidence']['recording_placeholder'])}",
        f"recording_capture_method: {context['evidence']['recording_capture_method']}",
        f"capture_scope: {context['evidence']['recording_capture_scope']}",
        f"failure_selection: {context['evidence']['recording_failure_selection']}",
        f"recording_failure_reason: {context['evidence']['recording_failure_reason'] or ''}",
        f"inspect_available: {context['evidence']['inspect_available']}",
        f"external_calls_available: {context['evidence']['external_calls_available']}",
        "",
        "Useful commands:",
        *context["useful_commands"],
        "",
        "Safety:",
        "Artifacts are local. Recordings may contain runtime data. Manifest env var values are excluded by default.",
        "",
    ])
    return "\n".join(lines)


def _run_agent_context(args: argparse.Namespace) -> int:
    if not args.latest:
        print("retrace agent-context failed: --latest is required for now", file=sys.stderr)
        return 1
    manifest = _latest_manifest(command="retrace agent-context")
    if manifest is None:
        return 1
    context = _agent_context(manifest)
    if args.json:
        print(render_json(context), end="")
    else:
        print(_render_agent_context(context), end="")
    return 0


def _launch_mcp(*, recording: Path | None = None, manifest: dict | None = None) -> int:
    if recording is not None:
        os.environ["RETRACE_RECORDING"] = str(recording)
    if manifest is not None and manifest.get("manifest_path"):
        os.environ["RETRACE_MANIFEST"] = str(manifest["manifest_path"])
    from retracesoftware.agent_mcp import main as agent_mcp_main

    return agent_mcp_main()


def _run_mcp(args: argparse.Namespace) -> int:
    if not args.latest:
        return _launch_mcp()
    resolved = _latest_recording_for_command(command="retrace mcp")
    if resolved is None:
        return 1
    manifest, recording_path = resolved
    return _launch_mcp(recording=recording_path, manifest=manifest)


def _parse_created_at(value: object) -> datetime:
    if not isinstance(value, str):
        return datetime.min.replace(tzinfo=UTC)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)


def _parse_duration(value: str) -> timedelta:
    units = {
        "s": "seconds",
        "m": "minutes",
        "h": "hours",
        "d": "days",
    }
    if len(value) < 2 or value[-1] not in units:
        raise ValueError("duration must use s, m, h, or d suffix, for example 7d")
    amount = int(value[:-1])
    if amount < 0:
        raise ValueError("duration must be non-negative")
    return timedelta(**{units[value[-1]]: amount})


def _confirm_clean(args: argparse.Namespace, count: int) -> bool:
    if args.yes:
        return True
    answer = input(f"Delete {count} Retrace failed-test run(s)? [y/N] ")
    return answer.lower() in {"y", "yes"}


def _safe_run_dir(runs_dir: Path, manifest: dict) -> Path | None:
    run_id = manifest.get("run_id")
    if not isinstance(run_id, str) or not run_id or Path(run_id).name != run_id:
        return None
    run_dir = runs_dir / run_id
    try:
        runs_resolved = runs_dir.resolve()
        if run_dir.is_symlink():
            return run_dir
        if run_dir.resolve().parent != runs_resolved:
            return None
    except OSError:
        return None
    return run_dir


def _delete_run_dir(run_dir: Path) -> None:
    if run_dir.is_symlink() or run_dir.is_file():
        run_dir.unlink()
        return
    shutil.rmtree(run_dir)


def _run_clean(args: argparse.Namespace) -> int:
    runs_dir = find_runs_dir() or get_default_runs_dir()
    manifests = list_runs()
    if not manifests:
        print(f"No Retrace failed-test runs found under {runs_dir}")
        return 0
    if not args.all and args.older_than is None and not args.latest:
        print("Choose runs to delete with --all, --older-than, or --latest.")
        return 1

    selected = manifests
    if args.latest:
        selected = manifests[:1]
    if args.older_than is not None:
        try:
            cutoff = datetime.now(UTC) - _parse_duration(args.older_than)
        except ValueError as exc:
            print(f"retrace clean failed: {exc}", file=sys.stderr)
            return 1
        selected = [
            manifest
            for manifest in selected
            if _parse_created_at(manifest.get("created_at")) < cutoff
        ]

    run_dirs = [run_dir for manifest in selected if (run_dir := _safe_run_dir(runs_dir, manifest)) is not None]
    if not run_dirs:
        print("No Retrace failed-test runs matched the cleanup criteria.")
        return 0
    if not _confirm_clean(args, len(run_dirs)):
        print("Retrace clean cancelled.")
        return 1
    for run_dir in run_dirs:
        _delete_run_dir(run_dir)
    print(f"Deleted {len(run_dirs)} Retrace failed-test run(s) from {runs_dir}")
    return 0


def _strip_separator(args: list[str]) -> list[str]:
    if args and args[0] == "--":
        return args[1:]
    return args


def _pytest_command(args: argparse.Namespace) -> list[str]:
    pytest_args = _strip_separator(list(args.pytest_args))
    command = [
        sys.executable,
        "-m",
        "retracesoftware",
    ]
    if args.verbose:
        command.append("--verbose")
    command.extend([
        "--recording",
        args.recording,
        "--format",
        args.format,
    ])
    if args.stacktraces:
        command.append("--stacktraces")
    if args.trace_shutdown:
        command.append("--trace_shutdown")
    if args.trace_inputs:
        command.append("--trace_inputs")
    if args.quit_on_error:
        command.append("--quit_on_error")
    command.extend(["--", "-m", "pytest", *pytest_args])
    return command


def _remove_passing_recording(recording: str) -> None:
    if recording == "disable":
        return
    path = Path(recording)
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def _run_pytest(args: argparse.Namespace) -> int:
    command = _pytest_command(args)
    result = subprocess.run(command)

    if result.returncode == 0:
        if not args.keep_passing:
            _remove_passing_recording(args.recording)
        return result.returncode

    if args.recording != "disable":
        print(
            f"\nRetrace recording saved: {args.recording}",
            file=sys.stderr,
        )
        if args.format == "unframed_binary":
            print(
                f"Replay with: {sys.executable} -m retracesoftware --recording {args.recording}",
                file=sys.stderr,
            )
        else:
            print(
                f"Open in VS Code: replay --recording {args.recording} --workspace",
                file=sys.stderr,
            )
    return result.returncode


def _run_inspect(args: argparse.Namespace) -> int:
    if args.latest:
        if args.recording:
            print("retrace inspect failed: pass either a recording path or --latest, not both", file=sys.stderr)
            return 1
        resolved = _latest_recording_for_command(command="retrace inspect")
        if resolved is None:
            return 1
        recording = str(resolved[1])
    else:
        recording = _resolve_recording(args, command="retrace inspect")
        if recording is None:
            return 1
    try:
        report = inspect_recording(
            recording,
            pid=args.pid,
            max_frames=args.max_frames,
            max_vars=args.max_vars,
            repr_budget=args.repr_budget,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace inspect failed: timed out after {exc.timeout} seconds", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"retrace inspect failed: could not inspect this recording. {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(render_json(report), end="")
    else:
        print(render_markdown(report), end="")
    if report["control"]["responses"]:
        return 0
    print(
        "retrace inspect could not inspect this recording yet. "
        "The recording resolved, but replay/control returned no inspectable state.",
        file=sys.stderr,
    )
    return 1


def _run_runs(args: argparse.Namespace) -> int:
    manifests = list_runs()
    if args.json:
        print(render_json(manifests), end="")
        return 0 if manifests else 1
    if not manifests:
        print("No Retrace failed-test runs found under .retrace/runs. Run pytest with --retrace first.", file=sys.stderr)
        return 1

    for manifest in manifests[: args.limit]:
        pytest = manifest.get("pytest") if isinstance(manifest.get("pytest"), dict) else {}
        failure = manifest.get("failure") if isinstance(manifest.get("failure"), dict) else {}
        node_id = pytest.get("node_id") or "<unknown test>"
        exception_type = failure.get("exception_type") or "unknown"
        exception_message = failure.get("exception_message") or failure.get("traceback_summary") or ""
        summary = exception_type if not exception_message else f"{exception_type}: {exception_message}"
        print(
            "\t".join([
                str(manifest.get("created_at") or ""),
                str(manifest.get("run_id") or ""),
                str(node_id),
                summary,
                str(manifest.get("recording_path") or ""),
            ]),
        )
    return 0


def _run_frame(args: argparse.Namespace) -> int:
    try:
        report = inspect_frame(
            args.recording,
            frame_index=args.frame,
            pid=args.pid,
            max_vars=args.max_vars,
            repr_budget=args.repr_budget,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace-agent frame timed out after {exc.timeout} seconds", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"retrace-agent frame failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(render_json(report), end="")
    else:
        print(render_frame_markdown(report), end="")
    return 0 if report["control"]["responses"] else 1


def _run_var(args: argparse.Namespace) -> int:
    try:
        report = inspect_variable(
            args.recording,
            frame_index=args.frame,
            name=args.name,
            pid=args.pid,
            repr_budget=args.repr_budget,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace-agent var timed out after {exc.timeout} seconds", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"retrace-agent var failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(render_json(report), end="")
    else:
        print(render_variable_markdown(report), end="")
    return 0 if report["control"]["responses"] else 1


def _run_provenance(args: argparse.Namespace) -> int:
    try:
        report = inspect_provenance(
            args.recording,
            frame_index=args.frame,
            name=args.name,
            pid=args.pid,
            repr_budget=args.repr_budget,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace-agent provenance timed out after {exc.timeout} seconds", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"retrace-agent provenance failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(render_json(report), end="")
    else:
        print(render_provenance_markdown(report), end="")
    return 0 if report["control"]["responses"] else 1


def _run_external_calls(args: argparse.Namespace) -> int:
    try:
        report = inspect_external_calls(
            args.recording,
            before_failure=args.before_failure,
            pid=args.pid,
            limit=args.limit,
            repr_budget=args.repr_budget,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace-agent external-calls timed out after {exc.timeout} seconds", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"retrace-agent external-calls failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(render_json(report), end="")
    else:
        print(render_external_calls_markdown(report), end="")
    return 0 if report["control"]["responses"] else 1


def _run_external_call(args: argparse.Namespace) -> int:
    try:
        report = inspect_external_call(
            args.recording,
            index=args.index,
            before_failure=args.before_failure,
            pid=args.pid,
            repr_budget=args.repr_budget,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace-agent external-call timed out after {exc.timeout} seconds", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"retrace-agent external-call failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(render_json(report), end="")
    else:
        print(render_external_call_markdown(report), end="")
    return 0 if report["control"]["responses"] else 1


def _build_pytest_parser() -> argparse.ArgumentParser:
    pytest_parser = argparse.ArgumentParser(
        prog="retrace pytest",
        description=(
            "Run pytest under Retrace. Passing recordings are discarded by "
            "default; failing recordings are kept for replay."
        ),
    )
    pytest_parser.add_argument(
        "--recording",
        default=os.environ.get("RETRACE_RECORDING", DEFAULT_PYTEST_RECORDING),
        help=f"Recording path (default: {DEFAULT_PYTEST_RECORDING})",
    )
    pytest_parser.add_argument(
        "--format",
        choices=("unframed_binary", "binary", "json"),
        default=DEFAULT_PYTEST_FORMAT,
        help=f"Recording format (default: {DEFAULT_PYTEST_FORMAT})",
    )
    pytest_parser.add_argument(
        "--no-stacktraces",
        dest="stacktraces",
        action="store_false",
        help="Do not capture stacktrace deltas for recorded events.",
    )
    pytest_parser.add_argument(
        "--keep-passing",
        action="store_true",
        help="Keep the recording even when pytest passes.",
    )
    pytest_parser.add_argument(
        "--trace-shutdown",
        dest="trace_shutdown",
        action="store_true",
        help="Trace Python shutdown and cleanup hooks.",
    )
    pytest_parser.add_argument(
        "--trace-inputs",
        dest="trace_inputs",
        action="store_true",
        help="Record call parameters for debugging.",
    )
    pytest_parser.add_argument(
        "--quit-on-error",
        dest="quit_on_error",
        action="store_true",
        help="Terminate on serialization errors instead of dropping them.",
    )
    pytest_parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable Retrace verbose output.",
    )
    pytest_parser.set_defaults(func=_run_pytest)
    return pytest_parser


def _build_inspect_parser(*, prog: str = "retrace-agent inspect") -> argparse.ArgumentParser:
    inspect_parser = argparse.ArgumentParser(
        prog=prog,
        description="Inspect observed replay/debugger state from a Retrace recording.",
    )
    inspect_parser.add_argument("recording", nargs="?", help="Retrace recording path")
    inspect_parser.add_argument("--latest", action="store_true", help="Inspect the latest failed-test run recording")
    inspect_parser.add_argument("--pid", help="PID to inspect when the recording has multiple processes")
    inspect_parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    inspect_parser.add_argument("--max-frames", type=int, default=5, help="Maximum application frames to print")
    inspect_parser.add_argument("--max-vars", type=int, default=50, help="Maximum locals to print")
    inspect_parser.add_argument("--repr-budget", type=int, default=300, help="Maximum repr characters per value")
    inspect_parser.set_defaults(func=_run_inspect)
    return inspect_parser


def _build_runs_parser(*, prog: str = "retrace runs") -> argparse.ArgumentParser:
    runs_parser = argparse.ArgumentParser(
        prog=prog,
        description="List recent failed-test Retrace runs from .retrace/runs.",
    )
    runs_parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    runs_parser.add_argument("--limit", type=int, default=20, help="Maximum runs to print")
    runs_parser.set_defaults(func=_run_runs)
    return runs_parser


def _build_agent_context_parser(*, prog: str = "retrace agent-context") -> argparse.ArgumentParser:
    context_parser = argparse.ArgumentParser(
        prog=prog,
        description="Print an evidence-only handoff for the latest failed-test run.",
    )
    context_parser.add_argument("--latest", action="store_true", help="Use the latest failed-test run")
    context_parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    context_parser.set_defaults(func=_run_agent_context)
    return context_parser


def _build_mcp_parser(*, prog: str = "retrace mcp") -> argparse.ArgumentParser:
    mcp_parser = argparse.ArgumentParser(
        prog=prog,
        description="Run the Retrace MCP server, optionally scoped to the latest failed-test run.",
    )
    mcp_parser.add_argument("--latest", action="store_true", help="Use the latest failed-test run recording")
    mcp_parser.set_defaults(func=_run_mcp)
    return mcp_parser


def _build_clean_parser(*, prog: str = "retrace clean") -> argparse.ArgumentParser:
    clean_parser = argparse.ArgumentParser(
        prog=prog,
        description="Delete local Retrace failed-test run artifacts from .retrace/runs.",
    )
    clean_parser.add_argument("--all", action="store_true", help="Delete all failed-test runs")
    clean_parser.add_argument("--latest", action="store_true", help="Delete only the latest failed-test run")
    clean_parser.add_argument("--older-than", help="Delete runs older than a duration such as 7d, 12h, or 30m")
    clean_parser.add_argument("--yes", action="store_true", help="Confirm deletion without prompting")
    clean_parser.set_defaults(func=_run_clean)
    return clean_parser


def _build_frame_parser(*, prog: str = "retrace-agent frame") -> argparse.ArgumentParser:
    frame_parser = argparse.ArgumentParser(
        prog=prog,
        description="Inspect bounded locals for one application frame from a Retrace recording.",
    )
    frame_parser.add_argument("recording", help="Retrace recording path")
    frame_parser.add_argument("--frame", type=int, required=True, help="Application frame index from inspect output")
    frame_parser.add_argument("--pid", help="PID to inspect when the recording has multiple processes")
    frame_parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    frame_parser.add_argument("--max-vars", type=int, default=50, help="Maximum locals to print")
    frame_parser.add_argument("--repr-budget", type=int, default=300, help="Maximum repr characters per value")
    frame_parser.set_defaults(func=_run_frame)
    return frame_parser


def _build_var_parser(*, prog: str = "retrace-agent var") -> argparse.ArgumentParser:
    var_parser = argparse.ArgumentParser(
        prog=prog,
        description="Inspect a bounded preview for one named local from a Retrace recording.",
    )
    var_parser.add_argument("recording", help="Retrace recording path")
    var_parser.add_argument("--frame", type=int, required=True, help="Application frame index from inspect output")
    var_parser.add_argument("--name", required=True, help="Named local variable to inspect")
    var_parser.add_argument("--pid", help="PID to inspect when the recording has multiple processes")
    var_parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    var_parser.add_argument("--repr-budget", type=int, default=300, help="Maximum repr characters for the value")
    var_parser.set_defaults(func=_run_var)
    return var_parser


def _build_provenance_parser(*, prog: str = "retrace-agent provenance") -> argparse.ArgumentParser:
    provenance_parser = argparse.ArgumentParser(
        prog=prog,
        description="Inspect stack provenance for one named local from a Retrace recording.",
    )
    provenance_parser.add_argument("recording", help="Retrace recording path")
    provenance_parser.add_argument("--frame", type=int, required=True, help="Application frame index from inspect output")
    provenance_parser.add_argument("--name", required=True, help="Named local variable to inspect")
    provenance_parser.add_argument("--pid", help="PID to inspect when the recording has multiple processes")
    provenance_parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    provenance_parser.add_argument("--repr-budget", type=int, default=300, help="Maximum repr characters for the value")
    provenance_parser.set_defaults(func=_run_provenance)
    return provenance_parser


def _build_external_calls_parser(*, prog: str = "retrace-agent external-calls") -> argparse.ArgumentParser:
    external_parser = argparse.ArgumentParser(
        prog=prog,
        description="Inspect bounded recorded external-call results from a Retrace recording.",
    )
    external_parser.add_argument("recording", help="Retrace recording path")
    external_parser.add_argument("--before-failure", action="store_true", help="Report calls observed before the stopped failure")
    external_parser.add_argument("--pid", help="PID to inspect when the recording has multiple processes")
    external_parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    external_parser.add_argument("--limit", type=int, default=20, help="Maximum external calls to print")
    external_parser.add_argument("--repr-budget", type=int, default=300, help="Maximum repr characters per value")
    external_parser.set_defaults(func=_run_external_calls)
    return external_parser


def _build_external_call_parser(*, prog: str = "retrace-agent external-call") -> argparse.ArgumentParser:
    external_parser = argparse.ArgumentParser(
        prog=prog,
        description="Inspect one expanded recorded external-call result from a Retrace recording.",
    )
    external_parser.add_argument("recording", help="Retrace recording path")
    external_parser.add_argument("--index", type=int, required=True, help="External call index from external-calls output")
    external_parser.add_argument("--before-failure", action="store_true", help="Report calls observed before the stopped failure")
    external_parser.add_argument("--pid", help="PID to inspect when the recording has multiple processes")
    external_parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    external_parser.add_argument("--repr-budget", type=int, default=4000, help="Maximum repr characters for the selected call")
    external_parser.set_defaults(func=_run_external_call)
    return external_parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retrace",
        description="Retrace workflow commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "pytest",
        help="Run pytest under Retrace and keep the recording on failure.",
    )
    subparsers.add_parser(
        "runs",
        help="List recent failed-test run manifests.",
    )
    subparsers.add_parser(
        "agent-context",
        help="Print an evidence-only handoff for the latest failed-test run.",
    )
    subparsers.add_parser(
        "mcp",
        help="Run the Retrace MCP server.",
    )
    subparsers.add_parser(
        "clean",
        help="Delete local failed-test run artifacts.",
    )
    subparsers.add_parser(
        "inspect",
        help="Inspect observed replay/debugger state from a recording.",
    )
    subparsers.add_parser(
        "frame",
        help="Inspect bounded locals for one application frame from a recording.",
    )
    subparsers.add_parser(
        "var",
        help="Inspect a bounded preview for one named local from a recording.",
    )
    subparsers.add_parser(
        "provenance",
        help="Inspect stack provenance for one named local from a recording.",
    )
    subparsers.add_parser(
        "external-calls",
        help="Inspect bounded recorded external-call results from a recording.",
    )
    subparsers.add_parser(
        "external-call",
        help="Inspect one expanded recorded external-call result from a recording.",
    )

    return parser


def _parse_and_run_inspect(argv: list[str], *, prog: str) -> int:
    inspect_parser = _build_inspect_parser(prog=prog)
    args = inspect_parser.parse_args(argv)
    return args.func(args)


def _parse_and_run_runs(argv: list[str], *, prog: str) -> int:
    runs_parser = _build_runs_parser(prog=prog)
    args = runs_parser.parse_args(argv)
    return args.func(args)


def _parse_and_run_agent_context(argv: list[str], *, prog: str) -> int:
    context_parser = _build_agent_context_parser(prog=prog)
    args = context_parser.parse_args(argv)
    return args.func(args)


def _parse_and_run_mcp(argv: list[str], *, prog: str) -> int:
    mcp_parser = _build_mcp_parser(prog=prog)
    args = mcp_parser.parse_args(argv)
    return args.func(args)


def _parse_and_run_clean(argv: list[str], *, prog: str) -> int:
    clean_parser = _build_clean_parser(prog=prog)
    args = clean_parser.parse_args(argv)
    return args.func(args)


def _parse_and_run_frame(argv: list[str], *, prog: str) -> int:
    frame_parser = _build_frame_parser(prog=prog)
    args = frame_parser.parse_args(argv)
    return args.func(args)


def _parse_and_run_var(argv: list[str], *, prog: str) -> int:
    var_parser = _build_var_parser(prog=prog)
    args = var_parser.parse_args(argv)
    return args.func(args)


def _parse_and_run_provenance(argv: list[str], *, prog: str) -> int:
    provenance_parser = _build_provenance_parser(prog=prog)
    args = provenance_parser.parse_args(argv)
    return args.func(args)


def _parse_and_run_external_calls(argv: list[str], *, prog: str) -> int:
    external_parser = _build_external_calls_parser(prog=prog)
    args = external_parser.parse_args(argv)
    return args.func(args)


def _parse_and_run_external_call(argv: list[str], *, prog: str) -> int:
    external_parser = _build_external_call_parser(prog=prog)
    args = external_parser.parse_args(argv)
    return args.func(args)


def main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "pytest":
        pytest_parser = _build_pytest_parser()
        pytest_argv = raw_argv[1:]
        if "--" in pytest_argv:
            separator = pytest_argv.index("--")
            args = pytest_parser.parse_args(pytest_argv[:separator])
            args.pytest_args = pytest_argv[separator + 1:]
        else:
            args, pytest_args = pytest_parser.parse_known_args(pytest_argv)
            args.pytest_args = pytest_args
        return args.func(args)
    if raw_argv and raw_argv[0] == "runs":
        return _parse_and_run_runs(raw_argv[1:], prog="retrace runs")
    if raw_argv and raw_argv[0] == "agent-context":
        return _parse_and_run_agent_context(raw_argv[1:], prog="retrace agent-context")
    if raw_argv and raw_argv[0] == "mcp":
        return _parse_and_run_mcp(raw_argv[1:], prog="retrace mcp")
    if raw_argv and raw_argv[0] == "clean":
        return _parse_and_run_clean(raw_argv[1:], prog="retrace clean")
    if raw_argv and raw_argv[0] == "inspect":
        return _parse_and_run_inspect(raw_argv[1:], prog="retrace inspect")
    if raw_argv and raw_argv[0] == "frame":
        return _parse_and_run_frame(raw_argv[1:], prog="retrace frame")
    if raw_argv and raw_argv[0] == "var":
        return _parse_and_run_var(raw_argv[1:], prog="retrace var")
    if raw_argv and raw_argv[0] == "provenance":
        return _parse_and_run_provenance(raw_argv[1:], prog="retrace provenance")
    if raw_argv and raw_argv[0] == "external-calls":
        return _parse_and_run_external_calls(raw_argv[1:], prog="retrace external-calls")
    if raw_argv and raw_argv[0] == "external-call":
        return _parse_and_run_external_call(raw_argv[1:], prog="retrace external-call")

    parser = _build_parser()
    parser.parse_args(raw_argv)
    return 2


def agent_main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "inspect":
        return _parse_and_run_inspect(raw_argv[1:], prog="retrace-agent inspect")
    if raw_argv and raw_argv[0] == "frame":
        return _parse_and_run_frame(raw_argv[1:], prog="retrace-agent frame")
    if raw_argv and raw_argv[0] == "var":
        return _parse_and_run_var(raw_argv[1:], prog="retrace-agent var")
    if raw_argv and raw_argv[0] == "provenance":
        return _parse_and_run_provenance(raw_argv[1:], prog="retrace-agent provenance")
    if raw_argv and raw_argv[0] == "external-calls":
        return _parse_and_run_external_calls(raw_argv[1:], prog="retrace-agent external-calls")
    if raw_argv and raw_argv[0] == "external-call":
        return _parse_and_run_external_call(raw_argv[1:], prog="retrace-agent external-call")
    parser = argparse.ArgumentParser(
        prog="retrace-agent",
        description="Agent-facing Retrace commands.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inspect", help="Inspect observed replay/debugger state from a recording.")
    subparsers.add_parser("frame", help="Inspect bounded locals for one application frame from a recording.")
    subparsers.add_parser("var", help="Inspect a bounded preview for one named local from a recording.")
    subparsers.add_parser("provenance", help="Inspect stack provenance for one named local from a recording.")
    subparsers.add_parser("external-calls", help="Inspect bounded recorded external-call results from a recording.")
    subparsers.add_parser("external-call", help="Inspect one expanded recorded external-call result from a recording.")
    parser.parse_args(raw_argv)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
