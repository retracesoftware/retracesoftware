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
from retracesoftware.proxy.old.record import RecordProxySystem
from retracesoftware.proxy.old.replay import ReplayProxySystem
import retracesoftware.stream as stream
from retracesoftware.install.startthread import thread_id
import datetime
import json
from shutil import copy2
import threading
import time
import gc
import hashlib

from retracesoftware.run import run_with_retrace
from retracesoftware.install import install_system, ImmutableTypes, thread_states
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

def run_create_tracedir_cmd(create_tracedir_cmd, path):
    import subprocess
    result = subprocess.run([create_tracedir_cmd, str(path)], capture_output=True, text=True)
    if result.returncode != 0:
        msg = f"create_tracedir_cmd '{create_tracedir_cmd}' failed with exit code {result.returncode}"
        if result.stdout:
            msg += f"\nstdout: {result.stdout}"
        if result.stderr:
            msg += f"\nstderr: {result.stderr}"
        raise ConfigurationError(msg)
    if not path.exists():
        raise ConfigurationError(f"create_tracedir_cmd '{create_tracedir_cmd}' exited successfully but directory '{path}' does not exist")

def record(options, args):
    
    # Check if recording is disabled (for performance testing)
    recording_disabled = (options.recording == 'disable')
    
    if options.verbose:
        if recording_disabled:
            print(f"Retrace enabled, recording DISABLED (performance testing mode)", file=sys.stderr)
        else:
            print(f"Retrace enabled, recording to {options.recording}", file=sys.stderr)

    if recording_disabled:
        path = None
    else:
        path = Path(expand_recording_path(options.recording))
        
        # Create trace directory via custom command or default mkdir
        if options.create_tracedir_cmd:
            run_create_tracedir_cmd(options.create_tracedir_cmd, path)
        else:
            path.mkdir(parents=True, exist_ok=True) 

    from retracesoftware.install import edgecases
    edgecases.recording_path = path

    # Write recording files (skip if disabled)
    if path:
        path_info = stream.get_path_info()
        dump_as_json(path / 'settings.json', {
            'argv': args,
            'executable': sys.executable,
            'magic_markers': options.magic_markers,
            'trace_inputs': options.trace_inputs,
            'trace_shutdown': options.trace_shutdown,
            'python_version': sys.version,
            'cwd': path_info['cwd'],
            'sys_path': path_info['sys_path'],
        })
        dump_as_json(path / 'md5_checksums.json', checksums())
        
        # Write env to standard .env file
        with open(path / '.env', 'w') as f:
            for key, value in os.environ.items():
                # Escape newlines and quotes for .env format
                escaped = value.replace('\\', '\\\\').replace('\n', '\\n').replace('"', '\\"')
                f.write(f'{key}="{escaped}"\n')

        dump_as_json(path / 'replay.code-workspace', vscode_workspace)

    # path=None disables all trace writes (performance testing mode)
    trace_path = None if recording_disabled else path / 'trace.bin'

    write_timeout = getattr(options, 'write_timeout', None)

    with stream.writer(path = trace_path,
                       thread = thread_id, 
                       verbose = options.verbose, 
                       magic_markers = options.magic_markers,
                       backpressure_timeout = write_timeout) as writer:

        if options.stacktraces:
            stackfactory = utils.StackFactory()
            stackfactory.exclude.add(record)
            stackfactory.exclude.add(main)
        else:
            stackfactory = None

        thread_state = utils.ThreadState(*thread_states)

        tracing_config = {}

        multiplier = 2
        gc.set_threshold(*map(lambda x: x * multiplier, gc.get_threshold()))

        system = RecordProxySystem(
            writer = writer,
            thread_state = thread_state,
            immutable_types = ImmutableTypes(), 
            tracing_config = tracing_config,
            maybe_collect = collector(multiplier = multiplier),
            traceargs = options.trace_inputs,
            stackfactory = stackfactory)

        # Exclude patchfindspec from stacktraces (install-layer concern)
        if stackfactory:
            from retracesoftware.install.patchfindspec import patch_find_spec
            system.exclude_from_stacktrace(patch_find_spec.__call__)

        # force a full collection
        install_system(system)

        gc.collect()
        gc.callbacks.append(system.on_gc_event)
    
        run_with_retrace(system, args, options.trace_shutdown)

        gc.callbacks.remove(system.on_gc_event)

def replay(args):
    # Resolve path before any chdir
    path = Path(args.recording).resolve()

    if not path.exists():
        raise RecordingNotFoundError(f"Recording path: {path} does not exist")

    settings = load_json(path / "settings.json")
    recorded_checksums = load_json(path / "md5_checksums.json")

    current_checksums = checksums()
    if recorded_checksums != current_checksums:
        diffs = diff_dicts(recorded_checksums, current_checksums)
        diff_str = "\n".join(diffs) if diffs else "(no differences found in structure)"
        raise VersionMismatchError(f"Checksums for Retrace do not match:\n{diff_str}")

    if settings['python_version'] != sys.version:
        raise VersionMismatchError("Python version does not match, cannot run replay with different version of Python to record")

    os.environ.update(load_env(path / '.env'))

    if sys.executable != settings['executable']:
        raise ConfigurationError(f"Stopping replay as current python executable: {sys.executable} is not what was used for record: {settings['executable']}")

    # Change to recorded cwd - replay from same directory as recording
    os.chdir(settings['cwd'])

    thread_state = utils.ThreadState(*thread_states)

    with stream.reader(path = path / 'trace.bin',
                        read_timeout = args.read_timeout,
                        verbose = args.verbose,
                        magic_markers = settings['magic_markers']) as reader:

        tracing_config = {}

        system = ReplayProxySystem(
            reader = reader,
            thread_state = thread_state,
            immutable_types = ImmutableTypes(), 
            tracing_config = tracing_config,
            traceargs = settings['trace_inputs'],
            verbose = args.verbose,
            skip_weakref_callbacks = args.skip_weakref_callbacks)

        install_system(system)

        gc.collect()
        gc.disable()

        # Use original argv - scripts are now relative to cwd (recording/run)
        run_with_retrace(system, settings['argv'], settings['trace_shutdown'])

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
        default = '.',
        help = 'the directory to place the recording files'
    )

    if '--' in sys.argv:
        parser.add_argument(
            '--stacktraces', 
            action='store_true', 
            help='Capture stacktrace for every event'
        )

        parser.add_argument(
            '--magic_markers', 
            action='store_true', 
            help='Write magic markers to tracefile, used for debugging'
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
            '--create_tracedir_cmd',
            type=str,
            default=None,
            help='Command to create trace directory (receives directory path as argument)'
        )

        parser.add_argument(
            '--write_timeout',
            type=float,
            default=None,
            help='Backpressure timeout in seconds. 0=drop immediately if persister is slow (production), '
                 '>0=wait up to N seconds then drop, None=wait forever (default)'
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

        replay(parser.parse_args())

if __name__ == "__main__":
    main()
