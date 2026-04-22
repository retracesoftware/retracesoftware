from contextlib import contextmanager
import datetime
import hashlib
import os
import stat
import sys
from pathlib import Path

import retracesoftware.functional as functional
import retracesoftware.stream as stream

from retracesoftware.exceptions import RecordingNotFoundError
from retracesoftware.proxy.tape import TapeWriter


def expand_recording_path(path):
    return datetime.datetime.now().strftime(path.format(pid=os.getpid()))


def normalize_recording_path(recording, argv):
    if recording is None:
        recording = "{script}.retrace"

    if "{script}" in recording:
        stem = Path(argv[0]).stem if argv else "recording"
        recording = recording.replace("{script}", stem)

    return recording


def is_fifo_path(path):
    try:
        return stat.S_ISFIFO(os.stat(path).st_mode)
    except OSError:
        return False


def file_md5(path):
    return hashlib.md5(path.read_bytes()).hexdigest()


def checksum(path):
    return file_md5(path) if path.is_file() else {
        entry.name: checksum(entry)
        for entry in path.iterdir()
        if entry.name != "__pycache__"
    }


def retrace_extension_paths():
    names = [
        "_retracesoftware_utils_release",
        "_retracesoftware_utils_debug",
        "_retracesoftware_functional_release",
        "_retracesoftware_functional_debug",
        "_retracesoftware_stream_release",
        "_retracesoftware_stream_debug",
    ]
    return {
        name: Path(sys.modules[name].__file__)
        for name in names
        if name in sys.modules
    }


def retrace_module_paths():
    paths = retrace_extension_paths()
    mod = sys.modules.get("retracesoftware")
    if mod is not None:
        mod_file = getattr(mod, "__file__", None)
        if mod_file is not None:
            paths["retracesoftware"] = Path(mod_file).parent
        else:
            for path in getattr(mod, "__path__", []):
                paths["retracesoftware"] = Path(path)
                break
    return paths


def checksums():
    return {name: checksum(path) for name, path in retrace_module_paths().items()}


def _find_replay_bin(explicit=None):
    if explicit:
        return str(Path(explicit).resolve())

    from_env = os.environ.get("REPLAY_BIN")
    if from_env:
        return str(Path(from_env).resolve())

    try:
        from retracesoftware.replay import binary_path
        return binary_path()
    except Exception:
        return None


def _write_shebang(trace_path, replay_bin):
    shebang = (
        f"#!{replay_bin} --recording\n"
        if replay_bin
        else "#!/usr/bin/env replay --recording\n"
    )
    with open(str(trace_path), "wb") as f:
        f.write(shebang.encode("utf-8"))
    os.chmod(str(trace_path), 0o755)


class _ReplayTapeReader:
    __slots__ = ("_tape_reader",)

    def __init__(self, *, path, read_timeout, verbose, start_offset=0):
        self._tape_reader = stream.TapeReader(
            path=path,
            read_timeout=read_timeout,
            verbose=verbose,
            start_offset=start_offset,
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self._tape_reader.close()

    def read(self):
        value = self._tape_reader.next()
        while isinstance(value, stream.Heartbeat):
            value = self._tape_reader.next()
        return value

    @property
    def messages_read(self):
        return self._tape_reader.messages_read


class RawTapeWriter:
    """Adapt raw protocol writes onto a native stream writer.

    ``proxy.io.recorder`` now emits raw protocol values directly. The native
    stream writer already knows how to serialize those values, including plain
    ``stream.Binding`` lookups, so this adapter is now just a small shape
    adapter that preserves the ``TapeWriter`` surface.
    """

    __slots__ = ("_tape_writer",)

    def __init__(self, tape_writer):
        self._tape_writer = tape_writer

    def write(self, *values):
        self._tape_writer.write(*values)


def create_tape_writer(options, argv, *, thread_getter) -> TapeWriter:
    recording_format = getattr(options, "format", "binary")
    recording = normalize_recording_path(options.recording, argv)
    recording_disabled = recording == "disable"

    if recording_disabled:
        trace_path = None
    else:
        trace_path = Path(expand_recording_path(recording))
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        replay_bin = _find_replay_bin(getattr(options, "replay_bin", None))
        if recording_format == "binary" and not is_fifo_path(trace_path):
            _write_shebang(trace_path, replay_bin)

    preamble = None
    if trace_path:
        path_info = stream.get_path_info()
        settings = {
            "argv": argv,
            "executable": sys.executable,
            "stacktraces": options.stacktraces,
            "trace_inputs": options.trace_inputs,
            "trace_shutdown": options.trace_shutdown,
            "monitor": getattr(options, "monitor", 0),
            "python_version": sys.version,
            "cwd": path_info["cwd"],
            "sys_path": path_info["sys_path"],
        }

        preamble = {
            "type": "exec",
            **settings,
            "checksums": checksums(),
            "env": dict(os.environ),
        }

    return stream.writer(
        path=trace_path,
        thread=thread_getter,
        format=recording_format,
        verbose=options.verbose,
        preamble=preamble,
        inflight_limit=options.inflight_limit,
        consumer_wait_timeout_ms=options.consumer_wait_timeout_ms,
        queue_capacity=options.queue_capacity,
        flush_interval=options.flush_interval,
        quit_on_error=options.quit_on_error,
        serialize_errors=not options.quit_on_error,
    )


@contextmanager
def open_tape_reader(args, *, thread_id):
    format_hint = getattr(args, "format", None)
    path = Path(args.recording).resolve()

    if not path.is_file():
        raise RecordingNotFoundError(f"Recording path: {path} is not a file")

    if format_hint == "unframed_binary":
        is_unframed = True
    elif format_hint in {"binary", "json"}:
        is_unframed = False
    else:
        is_unframed = stream.detect_raw_trace(path)

    if not is_unframed:
        raise RuntimeError("Python replay currently requires unframed_binary recordings")

    header, data_offset = stream.read_process_info(path, raw=is_unframed)

    with _ReplayTapeReader(
        path=path,
        read_timeout=args.read_timeout,
        verbose=args.verbose,
        start_offset=data_offset,
    ) as reader:
        yield header, reader


__all__ = [
    "RawTapeWriter",
    "checksums",
    "create_tape_writer",
    "expand_recording_path",
    "normalize_recording_path",
    "open_tape_reader",
]
