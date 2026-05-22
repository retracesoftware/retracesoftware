import sys
import os
import argparse
import json
import builtins
import runpy
import _thread
from dataclasses import dataclass
from pathlib import Path
import atexit
from contextlib import ExitStack


def _require_retrace_python():
    try:
        import retrace
    except ImportError:
        print(
            "retracesoftware requires retrace-python: retrace module not found",
            file=sys.stderr,
        )
        raise SystemExit(1)

    required = (
        "callbacks",
        "call_at",
        "CoordinateSpace",
        "coordinates",
        "disabled_space",
        "space_dispatch",
        "thread_delta",
    )
    missing = [name for name in required if not hasattr(retrace, name)]
    if missing:
        print(
            "retracesoftware requires retrace-python runtime: "
            f"retrace missing {', '.join(missing)}; running {sys.executable}",
            file=sys.stderr,
        )
        raise SystemExit(1)
    return retrace


retrace = _require_retrace_python()

import retracesoftware.utils as utils

from retracesoftware.proxy.tape import TapeReader
from retracesoftware.exceptions import RecordingNotFoundError, VersionMismatchError
from retracesoftware.proxy.system2 import System2
from retracesoftware.proxy.taggedtraceio import TaggedTraceReader, tagged_trace_writer
from retracesoftware.stream.reader import ExpectedBindMarker
from retracesoftware.run import run_python_command, wait_for_non_daemon_threads
from retracesoftware.tape import (
    checksums,
    create_tape_writer,
    normalize_recording_path,
    open_tape_reader,
)
from retracesoftware.install import install_retrace, patch_fork_for_replay

def diff_dicts(recorded, current, path=""):
    """Recursively diff two dicts, returning list of differences."""
    diffs = []
    all_keys = set(recorded.keys()) | set(current.keys())
    
    for key in sorted(all_keys):
        key_path = f"{path}.{key}" if path else key
        
        if key not in recorded:
            diffs.append(f"  + {key_path}: (new in current)")
        elif key not in current:
            diffs.append(f"  - {key_path}: (missing in current)")
        elif recorded[key] != current[key]:
            if isinstance(recorded[key], dict) and isinstance(current[key], dict):
                diffs.extend(diff_dicts(recorded[key], current[key], key_path))
            else:
                diffs.append(f"  ! {key_path}:")
                diffs.append(f"      recorded: {recorded[key][:16]}..." if isinstance(recorded[key], str) and len(recorded[key]) > 16 else f"      recorded: {recorded[key]}")
                diffs.append(f"      current:  {current[key][:16]}..." if isinstance(current[key], str) and len(current[key]) > 16 else f"      current:  {current[key]}")
    
    return diffs

class _ReplayStartupBinding:
    __slots__ = ()


def _bind_stream_chain(system, stream_obj, seen):
    current = stream_obj
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        system.is_bound.add(current)
        next_current = getattr(current, "buffer", None)
        if next_current is None:
            next_current = getattr(current, "raw", None)
        current = next_current


def _bind_record_runtime(system, tape_writer):
    seen = set()

    for stream_obj in (
        getattr(sys, "stdin", None),
        getattr(sys, "stdout", None),
        getattr(sys, "stderr", None),
        getattr(sys, "__stdin__", None),
        getattr(sys, "__stdout__", None),
        getattr(sys, "__stderr__", None),
    ):
        _bind_stream_chain(system, stream_obj, seen)

    for obj in (
        tape_writer,
        getattr(tape_writer, "_output", None),
        getattr(tape_writer, "_queue", None),
        getattr(getattr(tape_writer, "_output", None), "_stream", None),
    ):
        if obj is not None and id(obj) not in seen:
            seen.add(id(obj))
            system.is_bound.add(obj)

    output_stream = getattr(getattr(tape_writer, "_output", None), "_stream", None)
    _bind_stream_chain(system, output_stream, seen)


def _consume_replay_startup_bindings(system):
    """Consume CLI startup bind markers before ON_START.

    CLI recording binds a number of interpreter/runtime helper objects before
    entering ``System.run()``. Replay needs to consume those leading binding
    records so lifecycle hooks start aligned at ``ON_START``.
    """

    sentinels = []

    while True:
        sentinel = _ReplayStartupBinding()
        try:
            system.bind(sentinel)
        except ExpectedBindMarker:
            break
        else:
            sentinels.append(sentinel)

    return sentinels


@dataclass(frozen=True)
class Runner:
    argv: list[str]
    system: object
    options: argparse.Namespace
    env: dict[str, str] | None = None
    cwd: str | None = None
    sys_path: list[str] | None = None
    internal_space: object | None = None

    def __call__(self):
        uninstall = _setup_runner(self)
        try:
            return _run_target(self)
        finally:
            _finish_runner(self, uninstall)


def _setup_runner(runner):
    if runner.env is not None:
        os.environ.clear()
        os.environ.update(runner.env)
    if runner.cwd is not None:
        os.chdir(runner.cwd)
    if runner.sys_path is not None:
        sys.path[:] = runner.sys_path

    def runpy_exec(source, globals=None, locals=None):
        return runner.system.run_internal(builtins.exec, source, globals, locals)

    utils.update(
        runpy,
        "_run_code",
        utils.wrap_func_with_overrides,
        exec=runpy_exec,
    )

    uninstall = None
    try:
        strict_bind = None
        if runner.options.mode == "replay":
            strict_bind = runner.system.bind

            def bootstrap_bind(obj):
                try:
                    return strict_bind(obj)
                except ExpectedBindMarker:
                    return None

            runner.system.bind = bootstrap_bind

        try:
            uninstall = install_retrace(
                system=runner.system,
                monitor_level=getattr(runner.options, "monitor", 0),
                retrace_file_patterns=getattr(
                    runner.options,
                    "retrace_file_patterns",
                    None,
                ),
                verbose=runner.options.verbose,
                retrace_shutdown=runner.options.trace_shutdown,
            )
        finally:
            if strict_bind is not None:
                runner.system.bind = strict_bind

        if runner.options.mode == "replay" and getattr(
            runner.system,
            "consume_startup_bindings",
            True,
        ):
            fork_uninstall = patch_fork_for_replay(runner.system.disable_for)
            uninstall = utils.runall(fork_uninstall, uninstall)
            runner.system._startup_bindings = _consume_replay_startup_bindings(
                runner.system
            )
        elif runner.options.mode == "replay":
            fork_uninstall = patch_fork_for_replay(runner.system.disable_for)
            uninstall = utils.runall(fork_uninstall, uninstall)

        atexit.register(uninstall)
        return uninstall
    except BaseException:
        if uninstall is not None:
            uninstall()
        raise


def _run_enabled_target(runner):
    with runner.system.enable():
        return run_python_command(runner.argv)


def _run_target(runner):
    if runner.internal_space is not None:
        return runner.internal_space.apply(_run_enabled_target, runner)
    return _run_enabled_target(runner)


def _new_internal_retrace_space():
    return retrace.CoordinateSpace()


def _finish_runner(runner, uninstall):
    if uninstall is not None:
        runner.system.disable_for(wait_for_non_daemon_threads)()
        if not runner.options.trace_shutdown:
            atexit.unregister(uninstall)
            close_recording = getattr(runner.options, "close_recording", utils.noop)
            try:
                close_recording()
            finally:
                uninstall()
    elif not getattr(runner.options, "trace_shutdown", False):
        close_recording = getattr(runner.options, "close_recording", utils.noop)
        close_recording()

    controller = getattr(runner.options, "controller", None)
    resources = getattr(runner.options, "resources", None)
    try:
        if controller is not None:
            controller.on_replay_finished()
    finally:
        if resources is not None:
            resources.close()


def mode(options=None, argv=None):
    if options is not None:
        return options.mode

    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] and argv[0] in ("install", "uninstall"):
        return argv[0]
    if "--" in argv:
        return "record"
    return "replay"


def check_replay_header(header):
    recorded_checksums = header['checksums']
    current_checksums = checksums()
    if recorded_checksums != current_checksums:
        if os.environ.get('RETRACE_SKIP_CHECKSUMS'):
            print("WARNING: checksum mismatch ignored (RETRACE_SKIP_CHECKSUMS set)", file=sys.stderr)
        else:
            diffs = diff_dicts(recorded_checksums, current_checksums)
            diff_str = "\n".join(diffs) if diffs else "(no differences found in structure)"
            raise VersionMismatchError(f"Checksums for Retrace do not match:\n{diff_str}")

    if header['python_version'] != sys.version:
        raise VersionMismatchError("Python version does not match, cannot run replay with different version of Python to record")


def create_record_runner(options):
    argv = list(options.rest[1:])
    options.recording = normalize_recording_path(options.recording, argv)
    recording_disabled = options.recording == 'disable'

    if options.verbose:
        if recording_disabled:
            print("Retrace enabled, recording DISABLED (performance testing mode)", file=sys.stderr)
        else:
            print(f"Retrace enabled, recording to {options.recording}", file=sys.stderr)

    tape_writer = create_tape_writer(options, argv, thread_getter=_thread.get_ident)
    writer_closed = False

    def close_tape_writer():
        nonlocal writer_closed
        if writer_closed:
            return
        writer_closed = True
        tape_writer.__exit__(None, None, None)

    options.close_recording = close_tape_writer
    if options.trace_shutdown:
        # Writer shutdown runs after atexit uninstall so traced shutdown
        # events reach disk before the queue closes.
        atexit.register(close_tape_writer)

    try:
        system = System2.record_system(
            writer=tagged_trace_writer(tape_writer),
            debug=options.stacktraces,
        )
        internal_space = system.internal_space
        _bind_record_runtime(system, tape_writer)
        if hasattr(tape_writer, "enable_heartbeat"):
            flush_interval = getattr(options, "flush_interval", None)
            tape_writer.enable_heartbeat(flush_interval)
    except BaseException:
        close_tape_writer()
        raise

    return Runner(
        argv=argv,
        system=system,
        options=options,
        internal_space=internal_space,
    )


def create_replay_runner(options):
    resources = ExitStack()
    try:
        header, reader = resources.enter_context(
            open_tape_reader(options)
        )
        recorded_sys_path = header.get("sys_path")
        if not isinstance(recorded_sys_path, list):
            raise ValueError("recording header is missing sys_path")

        check_replay_header(header)
        options.resources = resources
        options.monitor = header.get('monitor', 0)
        options.trace_shutdown = header['trace_shutdown']

        chunk_ms = getattr(options, 'chunk_ms', None)
        if chunk_ms is not None:
            from retracesoftware.search import install_timeslice_search
            install_timeslice_search(
                chunk_ms=chunk_ms,
                get_offset=lambda: reader.messages_read,
            )

        controller = None
        controller_ref = [None]

        class TapeReaderAdapter:
            __slots__ = ["reader", "controller_ref"]

            def __init__(self, reader: TapeReader, controller_ref):
                self.reader = reader
                self.controller_ref = controller_ref

            def read(self):
                value = self.reader.read()
                controller = self.controller_ref[0]
                if controller is not None:
                    controller.on_new_message(value)
                return value

        stacktraces = header.get('stacktraces', False)

        tape_reader = TapeReaderAdapter(reader, controller_ref)
        system = System2.replay_system(
            reader=TaggedTraceReader(
                tape_reader,
                close=getattr(tape_reader, "close", None),
            ),
            debug=stacktraces,
        )
        internal_space = system.internal_space
        if hasattr(reader, "stub_factory"):
            reader.stub_factory = system.disable_for(reader.stub_factory)

        if getattr(options, 'control_socket', None) or getattr(options, 'stdio', False):
            from retracesoftware.control_runtime import Controller, UnixControlSocket, StdioControlSocket
            if getattr(options, 'stdio', False):
                import io
                _real_os_write = os.write
                _proto_fd = os.dup(sys.stdout.fileno())
                sys.stdout = sys.stderr

                class _RawFdWriter:
                    def write(self, data):
                        b = data.encode("utf-8") if isinstance(data, str) else data
                        _real_os_write(_proto_fd, b)
                        return len(data)

                    def flush(self):
                        pass

                _stdin_buf = io.StringIO(sys.stdin.read())
                ctrl_sock = StdioControlSocket(reader=_stdin_buf, writer=_RawFdWriter())
            else:
                ctrl_sock = UnixControlSocket(options.control_socket)

            def _before_fork():
                return reader._tape_reader.file_offset()

            def _after_fork(offset):
                reader._tape_reader.reopen(offset)

            controller = Controller(
                ctrl_sock,
                on_before_fork=_before_fork,
                on_after_fork=_after_fork,
                disable_for=system.disable_for,
                retrace_space=internal_space,
            )
            controller_ref[0] = controller

        options.controller = controller
        return Runner(
            argv=list(header["argv"]),
            env=dict(header["env"]),
            cwd=header.get("cwd"),
            sys_path=list(recorded_sys_path),
            system=system,
            options=options,
            internal_space=internal_space,
        )
    except BaseException:
        resources.close()
        raise


def list_pids(args):
    from retracesoftware import stream

    if args.recording is None:
        raise RecordingNotFoundError("Recording path is required for --list_pids")

    path = Path(args.recording)
    format_hint = getattr(args, "format", None)
    is_unframed = format_hint == "unframed_binary"
    if format_hint is None:
        is_unframed = stream.detect_raw_trace(path)

    if is_unframed:
        info, _ = stream.read_process_info(path, raw=True)
        print(json.dumps(info, separators=(",", ":")))
    else:
        for pid in sorted(stream.list_pids(path)):
            print(pid)


def pth_source():
    return Path(__file__).parent / 'retracesoftware_autoenable.pth'

def pth_target():
    import sysconfig
    return Path(sysconfig.get_paths()["purelib"]) / 'retracesoftware_autoenable.pth'

def cmd_install(args):
    """Install the .pth file so retrace auto-activates via RETRACE=1."""
    import shutil
    import stat
    source = pth_source()
    target = pth_target()
    shutil.copy(source, target)
    if hasattr(os, "chflags") and hasattr(stat, "UF_HIDDEN"):
        try:
            flags = os.stat(target).st_flags
            if flags & stat.UF_HIDDEN:
                os.chflags(target, flags & ~stat.UF_HIDDEN)
        except OSError:
            pass
    print(f'Retrace auto-enable installed: {target}')

def cmd_uninstall(args):
    """Remove the .pth file to disable auto-activation."""
    target = pth_target()
    if target.exists():
        target.unlink()
        print(f'Retrace auto-enable removed: {target}')
    else:
        print(f'Nothing to remove: {target} does not exist')

def _run_parser():
    parser = argparse.ArgumentParser(
        prog="python -m retracesoftware",
        description="Run a Python module with debugging, logging, etc."
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    parser.add_argument(
        '--recording',
        type = str,
        default = None,
        help = 'Trace file path (default: {script}.retrace)'
    )
    return parser


def _record_parser():
    parser = _run_parser()
    parser.add_argument(
        '--stacktraces',
        action='store_true',
        help='Capture stacktrace for every event'
    )

    parser.add_argument(
        '--trace_shutdown',
        action='store_true',
        help='Whether to trace system shutdown and cleanup hooks'
    )

    parser.add_argument(
        '--trace_inputs',
        action='store_true',
        help='Whether to write call parameters, used for debugging'
    )

    parser.add_argument(
        '--quit_on_error',
        action='store_true',
        help='Terminate on serialization errors instead of silently dropping them'
    )

    parser.add_argument(
        '--inflight_limit', type=int, default=128 * 1024 * 1024,
        help='Maximum bytes in-flight between writer and persister (default: 128MB)')

    parser.add_argument(
        '--queue_capacity', type=int, default=65536,
        help='Forward SPSC queue capacity (default: 65536)')

    parser.add_argument(
        '--consumer_wait_timeout_ms', type=int, default=10,
        help='Consumer wait timeout in milliseconds when the queue is below the notify threshold (default: 10)')

    parser.add_argument(
        '--flush_interval', type=float, default=0.1,
        help='Periodic flush interval in seconds (default: 0.1)')

    parser.add_argument(
        '--monitor', type=int, default=0,
        help='Monitoring level: 0=off (default), 1=PY calls/returns, 2=+C calls, 3=+LINE')

    parser.add_argument(
        '--retrace_file_patterns', type=str, default=None,
        help='Path to file with additional regex patterns for path-based retrace filtering')

    parser.add_argument(
        '--format', choices=('binary', 'unframed_binary', 'json'), default='binary',
        help='Recording backend format (default: binary)')

    parser.add_argument(
        '--replay_bin', type=str, default=None,
        help='Path to replay binary for trace file shebang (auto-detected if omitted)')

    parser.add_argument('rest', nargs = argparse.REMAINDER, help='target application and arguments')
    return parser


def _replay_parser():
    parser = _run_parser()
    parser.add_argument(
        '--skip_weakref_callbacks',
        action='store_true',
        help = 'whether to disable retrace in weakref callbacks on replay'
    )

    parser.add_argument(
        '--read_timeout',
        type = int,
        default = 1000,
        help = 'timeout in milliseconds for incomplete read of element to timeout'
    )

    parser.add_argument(
        '--retrace_file_patterns', type=str, default=None,
        help='Path to file with additional regex patterns for path-based retrace filtering')

    parser.add_argument(
        '--control_socket',
        type=str,
        default=None,
        help='Connect to Go replay control socket at this path')

    parser.add_argument(
        '--stdio',
        action='store_true',
        help='Read control commands from stdin, write responses to stdout')

    parser.add_argument(
        '--chunk_ms',
        type=float,
        default=None,
        help='Search for replay chunk boundaries every N milliseconds of execution time')

    parser.add_argument(
        '--list_pids',
        action='store_true',
        help='Print PIDs for framed traces, or the process preamble for unframed traces, then exit')

    parser.add_argument(
        '--format', choices=('binary', 'unframed_binary', 'json'), default=None,
        help='Optional recording format hint for replay input')
    return parser


def _command_parser():
    parser = argparse.ArgumentParser(
        prog="python -m retracesoftware",
        description="Retrace record/replay system"
    )
    sub = parser.add_subparsers(dest='command')
    sub.add_parser('install', help='Install .pth file for RETRACE=1 auto-activation')
    sub.add_parser('uninstall', help='Remove .pth file to disable auto-activation')
    return parser


def create_options(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    target_mode = mode(argv=argv)

    if target_mode in ("install", "uninstall"):
        options = _command_parser().parse_args(argv)
        options.mode = options.command
        return options

    if target_mode == "record":
        options = _record_parser().parse_args(argv)
        options.mode = target_mode
        return options

    options = _replay_parser().parse_args(argv)
    options.mode = "list_pids" if getattr(options, "list_pids", False) else "replay"
    return options


def create_runner(argv=None):
    options = create_options(argv)
    target_mode = mode(options)
    if target_mode == "install":
        return lambda: cmd_install(options)
    if target_mode == "uninstall":
        return lambda: cmd_uninstall(options)
    if target_mode == "list_pids":
        return lambda: list_pids(options)
    if target_mode == "record":
        return create_record_runner(options)
    if target_mode == "replay":
        return create_replay_runner(options)
    raise ValueError(f"unknown mode: {target_mode!r}")


def _create_runner_disabled():
    return retrace.disabled_space.apply(create_runner)


if __name__ == "__main__":
    _create_runner_disabled()()
