import sys
import os
import argparse
import json
import retracesoftware.functional as functional
import retracesoftware.utils as utils
from pathlib import Path
import gc
import atexit
from contextlib import contextmanager

from retracesoftware.threadid import ThreadId

from retracesoftware.proxy.tape import TapeReader
from retracesoftware.exceptions import VersionMismatchError
from retracesoftware.proxy.io import recorder, replayer
from retracesoftware.stream.reader import ExpectedBindMarker
from retracesoftware.run import run_python_command
from retracesoftware.tape import (
    checksums,
    create_tape_writer,
    normalize_recording_path,
    open_tape_reader,
)
from retracesoftware.install import install_and_run, install_retrace, patch_fork_for_replay

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

thread_id = ThreadId()


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
        getattr(tape_writer, "_heartbeat_lock", None),
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


@contextmanager
def _cli_module_overrides():
    yield

def record(options, args):
    options.recording = normalize_recording_path(options.recording, args)

    recording_disabled = (options.recording == 'disable')
    
    if options.verbose:
        if recording_disabled:
            print("Retrace enabled, recording DISABLED (performance testing mode)", file=sys.stderr)
        else:
            print(f"Retrace enabled, recording to {options.recording}", file=sys.stderr)

    flush_interval = getattr(options, "flush_interval", None)
    options.flush_interval = 0
    try:
        tape_writer = create_tape_writer(options, args, thread_getter=thread_id.id.get)
    finally:
        options.flush_interval = flush_interval
    heartbeat_lock = getattr(tape_writer, "_heartbeat_lock", None)
    if heartbeat_lock is not None:
        with heartbeat_lock:
            tape_writer._heartbeat_enabled = False
    writer_closed = False

    def close_tape_writer():
        nonlocal writer_closed
        if writer_closed:
            return
        writer_closed = True
        tape_writer.__exit__(None, None, None)

    if options.trace_shutdown:
        # Run writer shutdown after install_and_run()'s atexit uninstall so
        # traced shutdown events still make it to disk before the queue closes.
        atexit.register(close_tape_writer)

    try:
        system = recorder(
            writer=tape_writer,
            debug=options.stacktraces,
            stacktraces=options.stacktraces,
            gc_collect_multiplier=getattr(options, "gc_collect_multiplier", 0),
        )
        _bind_record_runtime(system, tape_writer)

        with _cli_module_overrides():
            install_and_run(
                system=system,
                options=options,
                function=run_python_command,
                args=(args,),
                post_install=None if options.trace_shutdown else close_tape_writer,
            )
    finally:
        if not options.trace_shutdown:
            close_tape_writer()

def replay(args):
    if getattr(args, 'list_pids', False):
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
        return

    chunk_ms = getattr(args, 'chunk_ms', None)
    control_socket_path = getattr(args, 'control_socket', None)
    use_stdio = getattr(args, 'stdio', False)

    with open_tape_reader(args, thread_id = thread_id) as (header, reader):
        if chunk_ms is not None:
            from retracesoftware.search import install_timeslice_search
            install_timeslice_search(
                chunk_ms=chunk_ms,
                get_offset=lambda: reader.messages_read,
            )

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

        os.environ.update(header['env'])

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
        system = replayer(
            next_object=tape_reader.read,
            close=getattr(tape_reader, "close", None),
            debug=stacktraces,
            stacktraces=stacktraces,
        )
        if hasattr(reader, "stub_factory"):
            reader.stub_factory = system.disable_for(reader.stub_factory)

        monitor_level = header.get('monitor', 0)
        replay_options = argparse.Namespace(
            **vars(args),
            monitor=monitor_level,
            trace_shutdown=header['trace_shutdown'],
        )

        if control_socket_path or use_stdio:
            from retracesoftware.control_runtime import Controller, UnixControlSocket, StdioControlSocket
            if use_stdio:
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
                ctrl_sock = UnixControlSocket(control_socket_path)

            def _before_fork():
                return reader._tape_reader.file_offset()

            def _after_fork(offset):
                reader._tape_reader.reopen(offset)

            controller = Controller(
                ctrl_sock,
                on_before_fork=_before_fork,
                on_after_fork=_after_fork,
                disable_for=system.disable_for,
            )
            controller_ref[0] = controller

        try:
            with _cli_module_overrides():
                strict_bind = system.bind

                def bootstrap_bind(obj):
                    try:
                        return strict_bind(obj)
                    except ExpectedBindMarker:
                        return None

                system.bind = bootstrap_bind
                try:
                    uninstall = install_retrace(
                        system=system,
                        monitor_level=getattr(replay_options, "monitor", 0),
                        retrace_file_patterns=getattr(replay_options, "retrace_file_patterns", None),
                        verbose=replay_options.verbose,
                        retrace_shutdown=replay_options.trace_shutdown,
                    )
                finally:
                    system.bind = strict_bind

                uninstall = utils.runall(
                    patch_fork_for_replay(system.disable_for),
                    uninstall,
                )

                system._startup_bindings = _consume_replay_startup_bindings(system)

                if replay_options.trace_shutdown:
                    atexit.register(uninstall)
                    system.run(run_python_command, header["argv"])
                else:
                    try:
                        system.run(run_python_command, header["argv"])
                    finally:
                        uninstall()
        finally:
            if controller:
                controller.on_replay_finished()

def pth_source():
    return Path(__file__).parent / 'retracesoftware_autoenable.pth'

def pth_target():
    import sysconfig
    return Path(sysconfig.get_paths()["purelib"]) / 'retracesoftware_autoenable.pth'

def cmd_install(args):
    """Install the .pth file so retrace auto-activates via RETRACE=1."""
    import shutil
    source = pth_source()
    target = pth_target()
    shutil.copy(source, target)
    print(f'Retrace auto-enable installed: {target}')

def cmd_uninstall(args):
    """Remove the .pth file to disable auto-activation."""
    target = pth_target()
    if target.exists():
        target.unlink()
        print(f'Retrace auto-enable removed: {target}')
    else:
        print(f'Nothing to remove: {target} does not exist')

def main():
    # Check for "install" or "uninstall" subcommands first
    if len(sys.argv) >= 2 and sys.argv[1] in ('install', 'uninstall'):
        parser = argparse.ArgumentParser(
            prog="python -m retracesoftware",
            description="Retrace record/replay system"
        )
        sub = parser.add_subparsers(dest='command')
        sub.add_parser('install', help='Install .pth file for RETRACE=1 auto-activation')
        sub.add_parser('uninstall', help='Remove .pth file to disable auto-activation')
        
        args = parser.parse_args()
        if args.command == 'install':
            cmd_install(args)
        elif args.command == 'uninstall':
            cmd_uninstall(args)
        return

    # Otherwise: record/replay mode
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

    if '--' in sys.argv:
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
            '--gc_collect_multiplier',
            type=int,
            default=0,
            help='Trigger replayable GC collection at intercepted safe points; 0 disables it')

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

        args = parser.parse_args()

        record(args, args.rest[1:])

    else:

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

        args = parser.parse_args()
        replay(args)

if __name__ == "__main__":
    main()
