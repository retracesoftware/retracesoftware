"""User-facing Retrace workflow commands."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from retracesoftware.agent_diagnose import (
    diagnose_recording,
    render_diagnosis_markdown,
    render_json as render_diagnosis_json,
)
from retracesoftware.agent_inspect import (
    inspect_expression,
    inspect_failures,
    inspect_function_code,
    inspect_recording,
    render_expression_markdown,
    render_failures_markdown,
    render_json,
    render_function_code_markdown,
    render_markdown,
)
from retracesoftware.recording_context import (
    RecordingResolutionError,
    build_agent_context,
    render_agent_context_text,
    resolve_manifest,
    resolve_recording,
)
import retracesoftware.report_server as report_server
import retracesoftware.share_reports as share_reports
from retracesoftware.report_service import DEFAULT_MAX_PAYLOAD_BYTES, ReportService


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


def _run_diagnose(args: argparse.Namespace) -> int:
    recording = _resolve_args_recording(args, command="retrace diagnose")
    if recording is None:
        return 1
    if not _validate_recording(recording, command="retrace diagnose"):
        return 1
    try:
        diagnosis = diagnose_recording(
            str(recording),
            max_frames=args.max_frames,
            max_vars=args.max_vars,
            repr_budget=args.repr_budget,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace diagnose failed: timed out after {exc.timeout} seconds", file=sys.stderr)
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
        print(render_diagnosis_json(diagnosis), end="")
    else:
        print(render_diagnosis_markdown(diagnosis), end="")
    return 0


def _run_function_code(args: argparse.Namespace) -> int:
    recording = _resolve_args_recording(args, command="retrace function-code")
    if recording is None:
        return 1
    if not _validate_recording(recording, command="retrace function-code"):
        return 1
    try:
        report = inspect_function_code(
            str(recording),
            frame_index=args.frame,
            max_chars=args.max_chars,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace function-code failed: timed out after {exc.timeout} seconds", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            "Recording found, but function source was not available through the current CLI backend.\n"
            f"Details: {exc}\n"
            "Try opening this recording in VS Code, or start MCP with:\n"
            f"  retrace mcp --recording {recording}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(render_json(report), end="")
    else:
        print(render_function_code_markdown(report), end="")
    return 0


def _run_eval(args: argparse.Namespace) -> int:
    recording = _resolve_args_recording(args, command="retrace eval")
    if recording is None:
        return 1
    if not _validate_recording(recording, command="retrace eval"):
        return 1
    try:
        report = inspect_expression(
            str(recording),
            frame_index=args.frame,
            expression=args.expression,
            repr_budget=args.repr_budget,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace eval failed: timed out after {exc.timeout} seconds", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            "Recording found, but expression evaluation was not available through the current CLI backend.\n"
            f"Details: {exc}\n"
            "Try opening this recording in VS Code, or start MCP with:\n"
            f"  retrace mcp --recording {recording}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(render_json(report), end="")
    else:
        print(render_expression_markdown(report), end="")
    return 0


def _run_failures(args: argparse.Namespace) -> int:
    recording = _resolve_args_recording(args, command="retrace failures")
    if recording is None:
        return 1
    if not _validate_recording(recording, command="retrace failures"):
        return 1
    try:
        report = inspect_failures(
            str(recording),
            limit=args.limit,
        )
    except subprocess.TimeoutExpired as exc:
        print(f"retrace failures failed: timed out after {exc.timeout} seconds", file=sys.stderr)
        return 1
    except OSError as exc:
        print(
            "Recording found, but failure search was not available through the current CLI backend.\n"
            f"Details: {exc}\n"
            "Try opening this recording in VS Code, or start MCP with:\n"
            f"  retrace mcp --recording {recording}",
            file=sys.stderr,
        )
        return 1

    if args.json:
        print(render_json(report), end="")
    else:
        print(render_failures_markdown(report), end="")
    return 0


def _confirm_share_full(args: argparse.Namespace) -> bool:
    if args.yes_share_full:
        return True
    if not sys.stdin.isatty():
        print(
            "retrace report failed: --share-full requires --yes-share-full in non-interactive mode.",
            file=sys.stderr,
        )
        return False
    print(
        "Warning: full diagnostic reports may contain sensitive runtime values, "
        "local paths, source snippets and tool calls.",
        file=sys.stderr,
    )
    print("Share only with trusted recipients.", file=sys.stderr)
    print("", file=sys.stderr)
    print("Anyone with the link can view the full diagnostic report.", file=sys.stderr)
    print("", file=sys.stderr)
    answer = input("Continue? [y/N] ")
    return answer.lower() in {"y", "yes"}


def _path_arg(value: str | None) -> Path | None:
    return Path(value) if value else None


def _write_public_outputs(args: argparse.Namespace, rendered: share_reports.RenderedReport) -> list[Path]:
    written: list[Path] = []
    json_path = Path(args.public_json or share_reports.PUBLIC_JSON_DEFAULT)
    html_path = Path(args.public_html or share_reports.PUBLIC_HTML_DEFAULT)
    share_reports.write_json(json_path, rendered.report)
    share_reports.write_html(html_path, rendered.html)
    written.extend([json_path, html_path])
    markdown_path = _path_arg(args.public_markdown)
    if markdown_path is not None:
        share_reports.write_markdown(markdown_path, rendered.markdown)
        written.append(markdown_path)
    return written


def _write_full_outputs(args: argparse.Namespace, rendered: share_reports.RenderedReport) -> list[Path]:
    written: list[Path] = []
    json_path = _path_arg(args.full_json)
    html_path = _path_arg(args.full_html)
    markdown_path = _path_arg(args.full_markdown)
    if json_path is not None:
        share_reports.write_json(json_path, rendered.report)
        written.append(json_path)
    if html_path is not None:
        share_reports.write_html(html_path, rendered.html)
        written.append(html_path)
    if markdown_path is not None:
        share_reports.write_markdown(markdown_path, rendered.markdown)
        written.append(markdown_path)
    return written


def _print_written(paths: list[Path], *, label: str) -> None:
    if not paths:
        return
    print(f"{label}:")
    for path in paths:
        print(f"  {path}")


def _delete_url(endpoint: str, report_id: str) -> str:
    return endpoint.rstrip("/") + "/" + report_id


def _print_uploaded_report(label: str, uploaded: share_reports.UploadResult, *, endpoint: str) -> None:
    print(label)
    print(uploaded.url)
    if uploaded.delete_token:
        print("Delete token:")
        print(uploaded.delete_token)
        print("Delete command:")
        print(f"curl -X DELETE -H 'Authorization: Bearer {uploaded.delete_token}' {_delete_url(endpoint, uploaded.report_id)}")
        print("Keep this token; it is the delete credential for this unlisted report.")


def _run_report(args: argparse.Namespace) -> int:
    if not args.share and not args.share_full and not any([
        args.public_json,
        args.public_html,
        args.public_markdown,
        args.full_json,
        args.full_html,
        args.full_markdown,
    ]):
        print(
            "retrace report failed: choose --share, --share-full, or at least one output path.",
            file=sys.stderr,
        )
        return 1
    if not args.json:
        print(
            "retrace report failed: --json <path> is required for this MVP slice.",
            file=sys.stderr,
        )
        return 1

    input_path = Path(args.json)
    print("Using AI report artifact:")
    print(f"  {input_path}")

    try:
        artifact = share_reports.load_artifact(input_path)
    except share_reports.ReportError as exc:
        print(f"retrace report failed: {exc}", file=sys.stderr)
        return 1

    repo_root = Path(args.repo_root) if args.repo_root else None
    api_key = os.environ.get("RETRACE_API_KEY", "")

    public_rendered: share_reports.RenderedReport | None = None
    if args.share or args.public_json or args.public_html or args.public_markdown:
        try:
            public_rendered = share_reports.render_public_report(
                artifact,
                repo_root=repo_root,
                redaction=args.redaction,
            )
        except share_reports.ReportError as exc:
            print(f"retrace report failed: {exc}", file=sys.stderr)
            return 1
        _print_written(_write_public_outputs(args, public_rendered), label="Public report preview written")

    full_rendered: share_reports.RenderedReport | None = None
    if args.share_full or args.full_json or args.full_html or args.full_markdown:
        full_rendered = share_reports.render_full_report(artifact)
        _print_written(_write_full_outputs(args, full_rendered), label="Full diagnostic report written")

    if args.share_full:
        if full_rendered is None:
            print("retrace report failed: full report was not generated.", file=sys.stderr)
            return 1
        if not _confirm_share_full(args):
            print("Full diagnostic upload cancelled.", file=sys.stderr)
            return 1
        if share_reports.contains_critical_secret(full_rendered.report):
            print(
                "retrace report failed: full report upload blocked: "
                "possible private key or credential detected.",
                file=sys.stderr,
            )
            return 1

    if args.share:
        if public_rendered is None:
            print("retrace report failed: public report was not generated.", file=sys.stderr)
            return 1
        if not api_key:
            print(
                "Public report upload skipped: RETRACE_API_KEY is not configured.",
                file=sys.stderr,
            )
            print("Review the public preview before posting it publicly.", file=sys.stderr)
        else:
            try:
                uploaded = share_reports.upload_report(
                    public_rendered,
                    api_key=api_key,
                    endpoint=args.endpoint,
                )
            except share_reports.ReportError as exc:
                print(f"retrace report failed: {exc}", file=sys.stderr)
                return 1
            _print_uploaded_report("Public Retrace Resolution Report:", uploaded, endpoint=args.endpoint)

    if args.share_full:
        if not api_key:
            print(
                "Full diagnostic upload skipped: RETRACE_API_KEY is not configured.",
                file=sys.stderr,
            )
            return 1
        try:
            uploaded = share_reports.upload_report(
                full_rendered,
                api_key=api_key,
                endpoint=args.endpoint,
            )
        except share_reports.ReportError as exc:
            print(f"retrace report failed: {exc}", file=sys.stderr)
            return 1
        _print_uploaded_report("Full Retrace Diagnostic Report:", uploaded, endpoint=args.endpoint)

    return 0


def _report_server_api_keys(args: argparse.Namespace) -> list[str]:
    keys: list[str] = []
    keys.extend(args.api_key or [])
    env_keys = os.environ.get("RETRACE_REPORT_API_KEYS") or os.environ.get("RETRACE_API_KEY", "")
    keys.extend(key.strip() for key in env_keys.split(",") if key.strip())
    return keys


def _run_report_server(args: argparse.Namespace) -> int:
    api_keys = _report_server_api_keys(args)
    if not api_keys:
        print(
            "retrace report-server failed: set --api-key, RETRACE_REPORT_API_KEYS, or RETRACE_API_KEY.",
            file=sys.stderr,
        )
        return 1

    service = ReportService(
        Path(args.storage_root),
        api_keys=api_keys,
        base_url=args.base_url or f"http://{args.host}:{args.port}",
        max_payload_bytes=args.max_payload_bytes,
    )
    try:
        server = report_server.create_server(
            args.host,
            args.port,
            service,
            log_requests=args.log_requests,
        )
    except OSError as exc:
        print(f"retrace report-server failed: {exc}", file=sys.stderr)
        return 1

    display_host = args.host if args.host not in {"", "0.0.0.0"} else "127.0.0.1"
    if not args.base_url:
        service.base_url = f"http://{display_host}:{server.server_address[1]}"
    print(f"Retrace report service listening on {display_host}:{server.server_address[1]}")
    print(f"Storage root: {Path(args.storage_root)}")
    print(f"Upload endpoint: {service.base_url}/api/reports")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("")
        print("Retrace report service stopped.")
    finally:
        server.server_close()
    return 0


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


def _build_diagnose_parser(*, prog: str = "retrace diagnose") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Inspect a recording and emit an evidence-driven agent diagnosis loop.",
    )
    parser.add_argument("recording_path", nargs="?", help="Retrace recording path")
    _add_recording_arguments(parser)
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.add_argument("--max-frames", type=int, default=5, help="Maximum application frames to inspect")
    parser.add_argument("--max-vars", type=int, default=12, help="Maximum locals to inspect")
    parser.add_argument("--repr-budget", type=int, default=300, help="Maximum repr characters per value")
    parser.set_defaults(func=_run_diagnose)
    return parser


def _build_function_code_parser(*, prog: str = "retrace function-code") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Return source for the function containing a selected application frame.",
    )
    parser.add_argument("recording_path", nargs="?", help="Retrace recording path")
    _add_recording_arguments(parser)
    parser.add_argument("--frame", type=int, default=0, help="Application frame index")
    parser.add_argument("--max-chars", type=int, default=12000, help="Maximum source characters to return")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.set_defaults(func=_run_function_code)
    return parser


def _build_eval_parser(*, prog: str = "retrace eval") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Evaluate one expression in a selected application frame.",
    )
    parser.add_argument("recording_path", nargs="?", help="Retrace recording path")
    _add_recording_arguments(parser)
    parser.add_argument("--frame", type=int, default=0, help="Application frame index")
    parser.add_argument("--expression", required=True, help="Expression to evaluate in the selected frame")
    parser.add_argument("--repr-budget", type=int, default=1200, help="Maximum repr characters per value")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.set_defaults(func=_run_eval)
    return parser


def _build_failures_parser(*, prog: str = "retrace failures") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Search replay for raised exception candidates and their cursors.",
    )
    parser.add_argument("recording_path", nargs="?", help="Retrace recording path")
    _add_recording_arguments(parser)
    parser.add_argument("--limit", type=int, default=5000, help="Maximum exception candidates to collect")
    parser.add_argument("--json", action="store_true", help="Emit structured JSON")
    parser.set_defaults(func=_run_failures)
    return parser


def _build_report_parser(*, prog: str = "retrace report") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Render and optionally share Retrace AI debugger reports.",
    )
    parser.add_argument("--json", help="Input AI report artifact JSON")
    parser.add_argument("--share", action="store_true", help="Create a public shareable report")
    parser.add_argument("--share-full", action="store_true", help="Create a full diagnostic shareable report")
    parser.add_argument(
        "--yes-share-full",
        action="store_true",
        help="Confirm full diagnostic sharing in non-interactive contexts",
    )
    parser.add_argument("--repo-root", help="Repository root for path redaction")
    parser.add_argument(
        "--redaction",
        choices=("standard", "strict"),
        default="standard",
        help="Public report redaction mode",
    )
    parser.add_argument("--public-json", help=f"Public JSON preview path (default with --share: {share_reports.PUBLIC_JSON_DEFAULT})")
    parser.add_argument("--public-html", help=f"Public HTML preview path (default with --share: {share_reports.PUBLIC_HTML_DEFAULT})")
    parser.add_argument("--public-markdown", help="Public Markdown preview path")
    parser.add_argument("--full-json", help="Full diagnostic JSON output path")
    parser.add_argument("--full-html", help="Full diagnostic HTML output path")
    parser.add_argument("--full-markdown", help="Full diagnostic Markdown output path")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("RETRACE_REPORT_ENDPOINT", share_reports.DEFAULT_REPORT_ENDPOINT),
        help="Report upload endpoint",
    )
    parser.set_defaults(func=_run_report)
    return parser


def _build_report_server_parser(*, prog: str = "retrace report-server") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Run a local shareable report service for development.",
    )
    parser.add_argument("--host", default=os.environ.get("RETRACE_REPORT_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("RETRACE_REPORT_PORT", "8787")))
    parser.add_argument(
        "--storage-root",
        default=os.environ.get("RETRACE_REPORT_STORAGE", ".retrace/report-service"),
        help="Directory used to store report JSON, HTML, Markdown, and metadata",
    )
    parser.add_argument(
        "--api-key",
        action="append",
        help="Accepted upload API key. May be provided more than once.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("RETRACE_REPORT_BASE_URL"),
        help="Base URL returned in create-report responses",
    )
    parser.add_argument(
        "--max-payload-bytes",
        type=int,
        default=int(os.environ.get("RETRACE_REPORT_MAX_PAYLOAD_BYTES", str(DEFAULT_MAX_PAYLOAD_BYTES))),
    )
    parser.add_argument("--log-requests", action="store_true", help="Log HTTP requests to stderr")
    parser.set_defaults(func=_run_report_server)
    return parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="retrace", description="Retrace workflow commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("agent-context", help="Print an evidence-only recording handoff.")
    subparsers.add_parser("diagnose", help="Emit an evidence-driven agent diagnosis loop.")
    subparsers.add_parser("eval", help="Evaluate one expression in a selected application frame.")
    subparsers.add_parser("failures", help="Search replay for raised exception candidates.")
    subparsers.add_parser("function-code", help="Return source for a selected application frame.")
    subparsers.add_parser("mcp", help="Run the Retrace MCP server for a recording.")
    subparsers.add_parser("inspect", help="Inspect observed replay/debugger state from a recording.")
    subparsers.add_parser("report", help="Render and optionally share Retrace AI debugger reports.")
    subparsers.add_parser("report-server", help="Run a local shareable report service for development.")
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
    if argv and argv[0] == "diagnose":
        args = _build_diagnose_parser(prog=f"{prog} diagnose").parse_args(argv[1:])
        return args.func(args)
    if argv and argv[0] == "function-code":
        args = _build_function_code_parser(prog=f"{prog} function-code").parse_args(argv[1:])
        return args.func(args)
    if argv and argv[0] == "eval":
        args = _build_eval_parser(prog=f"{prog} eval").parse_args(argv[1:])
        return args.func(args)
    if argv and argv[0] == "failures":
        args = _build_failures_parser(prog=f"{prog} failures").parse_args(argv[1:])
        return args.func(args)
    if argv and argv[0] == "report":
        args = _build_report_parser(prog=f"{prog} report").parse_args(argv[1:])
        return args.func(args)
    if argv and argv[0] == "report-server":
        args = _build_report_server_parser(prog=f"{prog} report-server").parse_args(argv[1:])
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
    if raw_argv and raw_argv[0] == "diagnose":
        args = _build_diagnose_parser(prog="retrace-agent diagnose").parse_args(raw_argv[1:])
        return args.func(args)
    if raw_argv and raw_argv[0] == "function-code":
        args = _build_function_code_parser(prog="retrace-agent function-code").parse_args(raw_argv[1:])
        return args.func(args)
    if raw_argv and raw_argv[0] == "eval":
        args = _build_eval_parser(prog="retrace-agent eval").parse_args(raw_argv[1:])
        return args.func(args)
    if raw_argv and raw_argv[0] == "failures":
        args = _build_failures_parser(prog="retrace-agent failures").parse_args(raw_argv[1:])
        return args.func(args)
    parser = argparse.ArgumentParser(prog="retrace-agent", description="Agent-facing Retrace commands.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("diagnose", help="Emit an evidence-driven agent diagnosis loop.")
    subparsers.add_parser("eval", help="Evaluate one expression in a selected application frame.")
    subparsers.add_parser("failures", help="Search replay for raised exception candidates.")
    subparsers.add_parser("function-code", help="Return source for a selected application frame.")
    subparsers.add_parser("inspect", help="Inspect observed replay/debugger state from a recording.")
    parser.parse_args(raw_argv)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
