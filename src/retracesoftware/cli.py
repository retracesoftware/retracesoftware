"""User-facing Retrace workflow commands."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from retracesoftware.agent_inspect import (
    inspect_recording,
    render_json,
    render_markdown,
)
from retracesoftware.recording_context import (
    RecordingResolutionError,
    build_agent_context,
    render_agent_context_text,
    resolve_manifest,
    resolve_recording,
)


def _validate_recording(path: Path, *, command: str) -> bool:
    if not path.exists():
        print(f"{command} failed: recording does not exist: {path}", file=sys.stderr)
        return False
    if not path.is_file():
        print(f"{command} failed: recording is not a file: {path}", file=sys.stderr)
        return False
    return True


def _resolve_args_recording(args: argparse.Namespace, *, command: str) -> Path | None:
    recording_arg = getattr(args, "recording", None)
    positional_recording = getattr(args, "recording_path", None)
    if recording_arg is not None and positional_recording is not None:
        print(f"{command} failed: pass either a recording path or --recording, not both", file=sys.stderr)
        return None
    if recording_arg is None:
        recording_arg = positional_recording
    try:
        return resolve_recording(recording=recording_arg, latest=getattr(args, "latest", False))
    except RecordingResolutionError as exc:
        print(f"{command} failed: {exc}", file=sys.stderr)
        return None


def _resolve_args_manifest(args: argparse.Namespace, *, command: str) -> Path | None:
    try:
        return resolve_manifest(
            manifest=getattr(args, "manifest", None),
            latest=getattr(args, "latest", False),
        )
    except RecordingResolutionError as exc:
        print(f"{command} failed: {exc}", file=sys.stderr)
        return None


def _run_agent_context(args: argparse.Namespace) -> int:
    recording = _resolve_args_recording(args, command="retrace agent-context")
    if recording is None:
        return 1
    if not _validate_recording(recording, command="retrace agent-context"):
        return 1
    manifest = _resolve_args_manifest(args, command="retrace agent-context")
    if manifest is None and getattr(args, "manifest", None):
        return 1
    try:
        context = build_agent_context(recording, manifest)
    except RecordingResolutionError as exc:
        print(f"retrace agent-context failed: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(context, indent=2, sort_keys=True))
    else:
        print(render_agent_context_text(context), end="")
    return 0


def _launch_mcp_server(*, recording: Path | None = None, manifest: Path | None = None) -> int:
    if recording is not None:
        os.environ["RETRACE_RECORDING"] = str(recording)
    if manifest is not None:
        os.environ["RETRACE_MANIFEST"] = str(manifest)
    from retracesoftware.agent_mcp import main as agent_mcp_main

    return agent_mcp_main()


def _run_mcp(args: argparse.Namespace) -> int:
    recording = _resolve_args_recording(args, command="retrace mcp")
    if recording is None:
        return 1
    if not _validate_recording(recording, command="retrace mcp"):
        return 1
    manifest = _resolve_args_manifest(args, command="retrace mcp")
    if manifest is None and getattr(args, "manifest", None):
        return 1
    return _launch_mcp_server(recording=recording, manifest=manifest)


def _run_inspect(args: argparse.Namespace) -> int:
    recording = _resolve_args_recording(args, command="retrace inspect")
    if recording is None:
        return 1
    if not _validate_recording(recording, command="retrace inspect"):
        return 1
    try:
        report = inspect_recording(
            str(recording),
            pid=args.pid,
            max_frames=args.max_frames,
            max_vars=args.max_vars,
            repr_budget=args.repr_budget,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace inspect failed: timed out after {exc.timeout} seconds", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            "Recording found, but no inspectable state was available through the current CLI backend.\n"
            f"Details: {exc}\n"
            "Try opening this recording in VS Code, or start MCP with:\n"
            f"  retrace mcp --recording {recording}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(render_json(report), end="")
    else:
        print(render_markdown(report), end="")

    availability = report.get("availability") if isinstance(report, dict) else {}
    if not isinstance(availability, dict):
        availability = {}
    if any(
        bool(availability.get(key))
        for key in ("cursor_available", "exception_available", "locals_available")
    ):
        return 0
    print(
        "Recording found, but no inspectable state was available through the current CLI backend.\n"
        "Try opening this recording in VS Code, or start MCP with:\n"
        f"  retrace mcp --recording {recording}",
        file=sys.stderr,
    )
    return 1


def _add_recording_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--recording", help="Retrace recording path")
    parser.add_argument("--latest", action="store_true", help="Use .retrace/latest-recording.json")


def _build_agent_context_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retrace agent-context",
        description="Print an evidence-only context packet for a Retrace recording.",
    )
    _add_recording_arguments(parser)
    parser.add_argument("--manifest", help="Optional manifest JSON path")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.set_defaults(func=_run_agent_context)
    return parser


def _build_mcp_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="retrace mcp",
        description="Run the Retrace MCP server for a selected recording.",
    )
    _add_recording_arguments(parser)
    parser.add_argument("--manifest", help="Optional manifest JSON path")
    parser.set_defaults(func=_run_mcp)
    return parser


def _build_inspect_parser(*, prog: str = "retrace inspect") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Inspect observed replay/debugger state from a Retrace recording.",
    )
    parser.add_argument("recording_path", nargs="?", help="Retrace recording path")
    _add_recording_arguments(parser)
    parser.add_argument("--pid", help="PID to inspect when the recording has multiple processes")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--max-frames", type=int, default=5, help="Maximum application frames to print")
    parser.add_argument("--max-vars", type=int, default=50, help="Maximum locals to print")
    parser.add_argument("--repr-budget", type=int, default=300, help="Maximum repr characters per value")
    parser.set_defaults(func=_run_inspect)
    return parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrace", description="Retrace workflow commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("agent-context", help="Print an evidence-only recording handoff.")
    subparsers.add_parser("mcp", help="Run the Retrace MCP server for a recording.")
    subparsers.add_parser("inspect", help="Inspect observed replay/debugger state from a recording.")
    return parser


def _dispatch(argv: list[str], *, prog: str = "retrace") -> int:
    if argv and argv[0] == "agent-context":
        args = _build_agent_context_parser().parse_args(argv[1:])
        return args.func(args)
    if argv and argv[0] == "mcp":
        args = _build_mcp_parser().parse_args(argv[1:])
        return args.func(args)
    if argv and argv[0] == "inspect":
        args = _build_inspect_parser(prog=f"{prog} inspect").parse_args(argv[1:])
        return args.func(args)
    parser = _build_parser()
    parser.parse_args(argv)
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    return _dispatch(list(sys.argv[1:] if argv is None else argv), prog="retrace")


def agent_main(argv: Sequence[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv and raw_argv[0] == "inspect":
        args = _build_inspect_parser(prog="retrace-agent inspect").parse_args(raw_argv[1:])
        return args.func(args)
    parser = argparse.ArgumentParser(prog="retrace-agent", description="Agent-facing Retrace commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inspect", help="Inspect observed replay/debugger state from a recording.")
    parser.parse_args(raw_argv)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
