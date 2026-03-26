import sys
import os
import argparse
import stat
import retracesoftware.utils as utils
import retracesoftware.functional as functional
from pathlib import Path
from retracesoftware.proxy.messagestream import ReplayReader
import retracesoftware.stream as stream
import datetime
import gc
import hashlib

from retracesoftware.threadid import ThreadId

from retracesoftware.proxy.system import System

from retracesoftware.install import ThreadRunContext, run_with_context, stream_writer
from retracesoftware.exceptions import RecordingNotFoundError, VersionMismatchError

def expand_recording_path(path):
    return datetime.datetime.now().strftime(path.format(pid = os.getpid()))


def is_fifo_path(path):
    try:
        return stat.S_ISFIFO(os.stat(path).st_mode)
    except OSError:
        return False

def file_md5(path):
    return hashlib.md5(path.read_bytes()).hexdigest()

def checksum(path):
    return file_md5(path) if path.is_file() else {entry.name: checksum(entry) for entry in path.iterdir() if entry.name != '__pycache__'}

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

def retrace_extension_paths():
    names = ['_retracesoftware_utils_release', '_retracesoftware_utils_debug', 
             '_retracesoftware_functional_release', '_retracesoftware_functional_debug', 
             '_retracesoftware_stream_release', '_retracesoftware_stream_debug']
    return {name: Path(sys.modules[name].__file__) for name in names if name in sys.modules}

def retrace_module_paths():
    paths = retrace_extension_paths()
    mod = sys.modules.get('retracesoftware')
    if mod is not None:
        mod_file = getattr(mod, '__file__', None)
        if mod_file is not None:
            paths['retracesoftware'] = Path(mod_file).parent
        else:
            for p in getattr(mod, '__path__', []):
                paths['retracesoftware'] = Path(p)
                break
    return paths

def checksums():
    return {name: checksum(path) for name, path in retrace_module_paths().items()}

def _find_replay_bin(explicit=None):
    """Resolve the Go replay binary path for the trace file shebang.

    Search order: explicit arg, REPLAY_BIN env var, then
    retracesoftware.replay.binary_path() which finds (or builds)
    the Go binary from the sibling repo.
    """
    if explicit:
        return str(Path(explicit).resolve())

    from_env = os.environ.get('REPLAY_BIN')
    if from_env:
        return str(Path(from_env).resolve())

    try:
        from retracesoftware.replay import binary_path
        return binary_path()
    except Exception:
        return None

def _write_shebang(trace_path, replay_bin):
    """Prepend a shebang line to trace_path so it's self-describing.

    The VSCode extension reads this to locate the replay binary.
    The FramedWriter opens with O_APPEND so PID-framed data
    follows the shebang.
    """
    shebang = f'#!{replay_bin} --recording\n' if replay_bin else '#!/usr/bin/env replay --recording\n'
    with open(str(trace_path), 'wb') as f:
        f.write(shebang.encode('utf-8'))
    os.chmod(str(trace_path), 0o755)

thread_id = ThreadId()

def record(system, options, args):
    recording_format = getattr(options, 'format', 'binary')

    if options.recording is None:
        options.recording = '{script}.retrace'

    if '{script}' in options.recording:
        stem = Path(args[0]).stem if args else 'recording'
        options.recording = options.recording.replace('{script}', stem)

    recording_disabled = (options.recording == 'disable')
    
    if options.verbose:
        if recording_disabled:
            print("Retrace enabled, recording DISABLED (performance testing mode)", file=sys.stderr)
        else:
            print(f"Retrace enabled, recording to {options.recording}", file=sys.stderr)

    if recording_disabled:
        trace_path = None
    else:
        trace_path = Path(expand_recording_path(options.recording))
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        replay_bin = _find_replay_bin(getattr(options, 'replay_bin', None))
        # FIFOs must stay open for the full recording; pre-writing the shebang
        # would close the pipe early, causing the reader to see EOF before the
        # FramedWriter starts streaming the actual trace.
        if recording_format == 'binary' and not is_fifo_path(trace_path):
            _write_shebang(trace_path, replay_bin)

    preamble = None
    if trace_path:
        path_info = stream.get_path_info()
        settings = {
            'argv': args,
            'executable': sys.executable,
            'trace_inputs': options.trace_inputs,
            'trace_shutdown': options.trace_shutdown,
            'monitor': getattr(options, 'monitor', 0),
            'python_version': sys.version,
            'cwd': path_info['cwd'],
            'sys_path': path_info['sys_path'],
        }
        recorded_checksums = checksums()
        
        preamble = {
            'type': 'exec',
            **settings,
            'checksums': recorded_checksums,
            'env': dict(os.environ),
        }

            
    with stream.writer(path = trace_path,
                       thread = thread_id.id.get,
                       format = recording_format,
                       verbose = options.verbose,
                       preamble = preamble,
                       inflight_limit = options.inflight_limit,
                       consumer_wait_timeout_ms = options.consumer_wait_timeout_ms,
                       queue_capacity = options.queue_capacity,
                       flush_interval = options.flush_interval,
                       quit_on_error = options.quit_on_error,
                       serialize_errors = not options.quit_on_error) as writer:

        if options.stacktraces:
            stackfactory = utils.StackFactory()
            stackfactory.exclude.add(record)
            stackfactory.exclude.add(main)
        else:
            stackfactory = None
            
        def on_write_error(exc_type, exc_value, exc_tb):
            import traceback
            print(f'\nretrace: serialization error: {exc_type.__name__}: {exc_value}', file=sys.stderr)
            if exc_tb:
                print('\nTraceback (serialization error):', file=sys.stderr)
                traceback.print_tb(exc_tb, file=sys.stderr)
            print('\nCall stack:', file=sys.stderr)
            traceback.print_stack(file=sys.stderr)
            os._exit(1)

        pw = stream_writer(writer=writer, stackfactory=stackfactory, 
                           on_write_error = system.disable_for(on_write_error) if options.quit_on_error else None)
        
        context = system.record_context(
            writer=pw, 
            stacktraces = options.stacktraces)

        on_weakref_start = writer.handle('ON_WEAKREF_CALLBACK_START')
        on_weakref_end = writer.handle('ON_WEAKREF_CALLBACK_END')

        def wrap_callback(callback):
            return utils.observer(
                on_call=functional.lazy(on_weakref_start),
                on_result=functional.lazy(on_weakref_end),
                on_error=functional.lazy(on_weakref_end),
                function=callback)

        monitor_level = getattr(options, 'monitor', 0)
        if monitor_level > 0:
            monitor_handle = writer.handle('MONITOR')
            def _write_monitor(value):
                monitor_handle(value)
            monitor_fn = system.disable_for(_write_monitor)
        else:
            monitor_fn = None

        run_with_context(system = system, 
                         context = context,
                         argv = args, 
                         wrap_callback = wrap_callback,
                         thread_id=thread_id,
                         trace_shutdown=options.trace_shutdown,
                         monitor_level=monitor_level,
                         monitor_fn=monitor_fn,
                         retrace_file_patterns=getattr(options, 'retrace_file_patterns', None),
                         verbose=options.verbose)

def parse_breakpoint(s):
    """Parse 'file:line' or 'file:line:condition' into (path, line, condition)."""
    parts = s.split(':', maxsplit=2)
    if len(parts) < 2:
        raise ValueError(f"Invalid breakpoint format: {s!r} (expected file:line[:condition])")
    path = os.path.realpath(parts[0])
    line = int(parts[1])
    condition = parts[2] if len(parts) > 2 else None
    return path, line, condition

def parse_fork_path(s):
    """Parse fork path to binary string of '0' (parent) and '1' (child).

    Accepts raw binary ('1101'), bare keywords ('child', 'parent'),
    or run-length encoded ('child-4-2-2' → '11110011').
    """
    if not s:
        return ''
    if s == 'child':
        return '1' * 1000
    if s == 'parent':
        return ''
    if all(c in '01' for c in s):
        return s
    parts = s.split('-')
    bit = '1' if parts[0] == 'child' else '0'
    result = []
    for count in parts[1:]:
        result.append(bit * int(count))
        bit = '0' if bit == '1' else '1'
    return ''.join(result)

def make_replay_fork(proxied_fork, reader, fork_path):
    """Wrap a proxied os.fork to handle PID switching on replay.

    The orphaned RESULT(0) left in the child's stream is naturally
    skipped by ReplayReader.sync() on the next proxy call.
    """
    fork_index = [0]
    def replay_fork():
        child_pid = proxied_fork()
        follow_child = (fork_index[0] < len(fork_path)
                        and fork_path[fork_index[0]] == '1')
        fork_index[0] += 1
        if follow_child:
            reader.set_pid(child_pid)
            return 0
        return child_pid
    return replay_fork

def replay(system, args):

    chunk_ms = getattr(args, 'chunk_ms', None)
    control_socket_path = getattr(args, 'control_socket', None)
    use_stdio = getattr(args, 'stdio', False)
    format_hint = getattr(args, 'format', None)

    # Resolve path before any chdir.
    path = Path(args.recording).resolve()

    if not path.is_file():
        raise RecordingNotFoundError(f"Recording path: {path} is not a file")

    if format_hint == 'unframed_binary':
        is_unframed = True
    elif format_hint in {'binary', 'json'}:
        is_unframed = False
    else:
        is_unframed = stream.detect_raw_trace(path)
    if not is_unframed:
        raise RuntimeError("Python replay currently requires unframed_binary recordings")
    header, data_offset = stream.read_process_info(path, raw=is_unframed)

    with stream.reader(path = path,
                    read_timeout = args.read_timeout,
                    verbose = args.verbose,
                    start_offset = data_offset,
                    thread_id = thread_id) as reader:
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

            monitor_level = header.get('monitor', 0)

            per_thread_source = stream.per_thread(
                source=reader, thread = thread_id,
                timeout=max(1, args.read_timeout // 1000))
            msg_stream = ReplayReader(
                per_thread_source,
                bind=reader.bind,
                stub_factory=getattr(reader, "stub_factory", None),
                monitor_enabled=(monitor_level > 0),
            )

            controller = None
            if control_socket_path or use_stdio:
                from retracesoftware.control_runtime import Controller, UnixControlSocket, StdioControlSocket
                if use_stdio:
                    import io
                    # The replay proxy patches file types and os module
                    # functions at the class level, so any normal
                    # TextIOWrapper / os.write will be intercepted.
                    # Capture the real os.write and dup stdout's fd
                    # BEFORE the proxy activates so protocol I/O is
                    # completely invisible to the replay.
                    _real_os_write = os.write
                    _proto_fd = os.dup(sys.stdout.fileno())
                    sys.stdout = sys.stderr

                    class _RawFdWriter:
                        """Bypass proxy by writing directly via captured os.write."""
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
                    offset = reader.file_offset()
                    reader.close()
                    return offset

                def _after_fork(offset):
                    reader.reopen(offset)

                controller = Controller(
                    ctrl_sock,
                    on_before_fork=_before_fork,
                    on_after_fork=_after_fork,
                    disable_for=system.disable_for,
                )
                _original_sync = msg_stream.sync
                def _sync():
                    _original_sync()
                    controller.on_new_message(None)
                msg_stream.sync = _sync

            context = system.replay_context(reader=msg_stream)

            if monitor_level > 0:
                def _verify_monitor(value):
                    msg_stream.monitor_checkpoint(value)
                monitor_fn = system.disable_for(_verify_monitor)
            else:
                monitor_fn = None

            # During replay, weakref callbacks fire naturally and handle
            # messages (ON_WEAKREF_CALLBACK_START/END) on the tape are
            # auto-skipped by the replay reader, so wrap_callback is identity.
            def wrap_callback(callback):
                return callback

            # GC execution is captured during record and replayed
            # deterministically, so disable automatic GC to prevent
            # nondeterministic collections that would desync the replay.
            # Flush any pending garbage first.
            gc.collect()
            gc.disable()

            def on_ready():
                _gate_fork = os.fork

                def post_fork_replay(recorded_result):
                    if recorded_result == 0:
                        pid = system.disable_for(_gate_fork)()
                        if pid != 0:
                            system.disable_for(os._exit)(0)
                    return recorded_result

                os.fork = functional.sequence(_gate_fork, post_fork_replay)

            try:
                run_with_context(system=system, thread_id=thread_id,
                                context=context, argv=header['argv'],
                                wrap_callback=wrap_callback,
                                trace_shutdown=header['trace_shutdown'],
                                monitor_level=monitor_level,
                                monitor_fn=monitor_fn,
                                retrace_file_patterns=getattr(args, 'retrace_file_patterns', None),
                                verbose=args.verbose,
                                on_ready=on_ready,
                                child_context_factory=lambda: ThreadRunContext(context))
            except Exception:
                raise
            finally:
                gc.enable()
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

    system = System()

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

        record(system, args, args.rest[1:])

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
            '--fork_path',
            type = str,
            default = '',
            help = 'Fork path: binary string (e.g. "1101") or RLE (e.g. "child-2-1-1"). '
                   '0=parent, 1=child at each fork point.'
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
            '--format', choices=('binary', 'unframed_binary', 'json'), default=None,
            help='Optional recording format hint for replay input')

        args = parser.parse_args()
        replay(system, args)

if __name__ == "__main__":
    main()
