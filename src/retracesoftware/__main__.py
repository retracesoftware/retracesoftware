import re
import sys
import runpy
import os
import argparse
from typing import Tuple, List
import retracesoftware.utils as utils
import retracesoftware.functional as functional
from retracesoftware.install.stackdifference import on_stack_difference
from pathlib import Path
from retracesoftware.proxy.messagestream import MessageStream
import retracesoftware.stream as stream
import datetime
import json
from shutil import copy2
import threading
import time
import gc
import hashlib

from retracesoftware.proxy.system import System

from retracesoftware.run import run_with_retrace
from retracesoftware.install import run_with_context, stream_writer
from retracesoftware.exceptions import RecordingNotFoundError, VersionMismatchError, ConfigurationError

def expand_recording_path(path):
    return datetime.datetime.now().strftime(path.format(pid = os.getpid()))

def load_json(file):
    with open(file, "r", encoding="utf-8") as f:
        return json.load(f)

def dump_as_json(path, obj):
    with open(path, 'w') as f:
        json.dump(obj, f, indent=2)

def load_env(file):
    """Load a .env file into a dict."""
    env = {}
    with open(file, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                # Remove surrounding quotes if present
                if value.startswith('"') and value.endswith('"'):
                    value = value[1:-1]
                # Unescape
                value = value.replace('\\n', '\n').replace('\\"', '"').replace('\\\\', '\\')
                env[key] = value
    return env

vscode_workspace = {
    "folders": [{ 'path': '.' }],
    "settings": {
        "python.defaultInterpreterPath": sys.executable,
    },
    "launch": {
        "version": "0.2.0",
        "configurations": [{
            "name": "replay",
            "type": "debugpy",
            "request": "launch",
            
            "python": sys.executable,
            "module": "retracesoftware",
            "args": [
                "--recording", "..",
                "--skip_weakref_callbacks",
                "--read_timeout", "1000"
            ],
            
            "cwd": "${workspaceFolder}/run",
            "console": "integratedTerminal",
            "justMyCode": False
        }]
    },
}

def scriptname(argv):
    return argv[1] if argv[0] == "-m" else argv[0]

def collector(multiplier):
    collect_gen = utils.CollectPred(multiplier = multiplier)

    return functional.lazy(functional.sequence(collect_gen, functional.when_not_none(gc.collect)))

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
    paths['retracesoftware'] = Path(sys.modules['retracesoftware'].__file__).parent
    return paths

def checksums():
    return {name: checksum(path) for name, path in retrace_module_paths().items()}

def generate_workspace(workspace_path, settings, recorded_checksums, env):
    """Write VS Code workspace sidecar files to *workspace_path*."""
    workspace_path.mkdir(parents=True, exist_ok=True)
    dump_as_json(workspace_path / 'settings.json', settings)
    dump_as_json(workspace_path / 'md5_checksums.json', recorded_checksums)
    with open(workspace_path / '.env', 'w') as f:
        for key, value in env.items():
            escaped = value.replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')
            f.write(f'{key}="{escaped}"\n')
    dump_as_json(workspace_path / 'replay.code-workspace', vscode_workspace)

def record(options, args):
    
    # Check if recording is disabled (for performance testing)
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
        trace_path.touch(exist_ok=True)

    from retracesoftware.install import edgecases
    edgecases.recording_path = trace_path.parent if trace_path else None

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
            **settings,
            'checksums': recorded_checksums,
            'env': dict(os.environ),
        }
        
        workspace = getattr(options, 'workspace', None)
        if workspace:
            generate_workspace(
                Path(workspace), settings, recorded_checksums, dict(os.environ))

    write_timeout = getattr(options, 'write_timeout', None)

    with stream.writer(path = trace_path,
                       thread = utils.thread_id,
                       verbose = options.verbose, 
                       preamble = preamble,
                       backpressure_timeout = write_timeout,
                       append = True) as writer:

        if options.stacktraces:
            stackfactory = utils.StackFactory()
            stackfactory.exclude.add(record)
            stackfactory.exclude.add(main)
        else:
            stackfactory = None

        system = System()
        pw = stream_writer(writer=writer, stackfactory=stackfactory)
        context = system.record_context(
            writer=pw, stacktraces=options.stacktraces)

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

        run_with_context(system, context, args, wrap_callback,
                         trace_shutdown=options.trace_shutdown,
                         monitor_level=monitor_level,
                         monitor_fn=monitor_fn)

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
    skipped by MessageStream.sync() on the next proxy call.
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


def replay(args):
    # Resolve path before any chdir
    path = Path(args.recording).resolve()

    if not path.is_file():
        raise RecordingNotFoundError(f"Recording path: {path} is not a file")

    if getattr(args, 'list_pids', False):
        pids = stream.list_pids(path)
        for pid in sorted(pids):
            print(pid)
        return

    with stream.reader(path = path,
                        read_timeout = args.read_timeout,
                        verbose = args.verbose) as reader:

        # First object in the stream is the header dict with all replay metadata
        header = reader()

        recorded_checksums = header['checksums']
        current_checksums = checksums()
        if recorded_checksums != current_checksums:
            diffs = diff_dicts(recorded_checksums, current_checksums)
            diff_str = "\n".join(diffs) if diffs else "(no differences found in structure)"
            raise VersionMismatchError(f"Checksums for Retrace do not match:\n{diff_str}")

        if header['python_version'] != sys.version:
            raise VersionMismatchError("Python version does not match, cannot run replay with different version of Python to record")

        os.environ.update(header['env'])

        if sys.executable != header['executable']:
            raise ConfigurationError(f"Stopping replay as current python executable: {sys.executable} is not what was used for record: {header['executable']}")

        # Change to recorded cwd - replay from same directory as recording
        os.chdir(header['cwd'])

        system = System()

        monitor_level = header.get('monitor', 0)

        per_thread_source = stream.per_thread(
            source=reader, thread=utils.thread_id,
            timeout=max(1, args.read_timeout // 1000))
        msg_stream = MessageStream(per_thread_source,
                                   monitor_enabled=(monitor_level > 0))
        context = system.replay_context(reader=msg_stream)

        if monitor_level > 0:
            def _verify_monitor(value):
                msg_stream.monitor_checkpoint(value)
            monitor_fn = system.disable_for(_verify_monitor)
        else:
            monitor_fn = None

        # During replay, weakref callbacks fire naturally and handle
        # messages (ON_WEAKREF_CALLBACK_START/END) on the tape are
        # auto-skipped by MessageStream, so wrap_callback is identity.
        def wrap_callback(callback):
            return callback

        # GC execution is captured during record and replayed
        # deterministically, so disable automatic GC to prevent
        # nondeterministic collections that would desync the replay.
        # Flush any pending garbage first.
        gc.collect()
        gc.disable()

        fork_path = parse_fork_path(getattr(args, 'fork_path', ''))

        def install_fork_handler():
            if not fork_path:
                return
            import posix
            proxied_fork = posix.fork
            wrapper = make_replay_fork(proxied_fork, reader, fork_path)
            posix.fork = wrapper
            os.fork = wrapper

        run_with_context(system, context, header['argv'], wrap_callback,
                         header['trace_shutdown'], on_ready=install_fork_handler,
                         monitor_level=monitor_level,
                         monitor_fn=monitor_fn)
        gc.enable()

def pth_source():
    return Path(__file__).parent / 'retrace.pth'

def pth_target():
    import sysconfig
    return Path(sysconfig.get_paths()["purelib"]) / 'retrace.pth'

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
        default = 'trace.bin',
        help = 'Trace file path (default: trace.bin)'
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
            '--workspace',
            type=str,
            default=None,
            help='Generate VS Code workspace directory with sidecar files '
                 '(settings.json, .env, checksums, launch config)'
        )

        parser.add_argument(
            '--write_timeout',
            type=float,
            default=None,
            help='Backpressure timeout in seconds. 0=drop immediately if persister is slow (production), '
                 '>0=wait up to N seconds then drop, None=wait forever (default)'
        )

        parser.add_argument(
            '--monitor',
            type=int,
            default=0,
            help='Monitoring level for fine-grained divergence detection: '
                 '0=off (default), 1=PY calls/returns, 2=+C calls, 3=+LINE'
        )

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
            '--fork_path',
            type = str,
            default = '',
            help = 'Fork path: binary string (e.g. "1101") or RLE (e.g. "child-2-1-1"). '
                   '0=parent, 1=child at each fork point.'
        )

        parser.add_argument(
            '--list_pids',
            action = 'store_true',
            help = 'List all PIDs in the trace and exit'
        )

        replay(parser.parse_args())

if __name__ == "__main__":
    main()
