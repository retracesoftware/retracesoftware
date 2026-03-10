import importlib.resources
import os
import re
import tomllib


# ---------------------------------------------------------------------------
# Quantity parsing -- supports plain numbers and suffixed strings like
# "128MB", "5s", "100ms".  Used for both TOML values and env var overrides.
# ---------------------------------------------------------------------------

_QUANTITY_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([a-zA-Z]*)\s*$")
_SIZE_UNITS = {"b": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3, "tb": 1024**4}
_TIME_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}


def parse_size(val):
    if isinstance(val, (int, float)):
        return int(val)
    m = _QUANTITY_RE.match(str(val))
    if not m:
        raise ValueError(f"invalid size: {val!r}")
    num, unit = float(m.group(1)), m.group(2).lower()
    if not unit:
        return int(num)
    if unit not in _SIZE_UNITS:
        raise ValueError(f"unknown size unit: {unit!r}")
    return int(num * _SIZE_UNITS[unit])


def parse_duration(val):
    if isinstance(val, (int, float)):
        return float(val)
    m = _QUANTITY_RE.match(str(val))
    if not m:
        raise ValueError(f"invalid duration: {val!r}")
    num, unit = float(m.group(1)), m.group(2).lower()
    if not unit:
        return num
    if unit not in _TIME_UNITS:
        raise ValueError(f"unknown time unit: {unit!r}")
    return num * _TIME_UNITS[unit]


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

_ENV_OVERRIDES = {
    "RETRACE_VERBOSE":        ("record", "verbose", bool),
    "RETRACE_STACKTRACES":    ("record", "stacktraces", bool),
    "RETRACE_SHUTDOWN":       ("record", "trace_shutdown", bool),
    "RETRACE_TRACE_INPUTS":   ("record", "trace_inputs", bool),
    "RETRACE_RECORDING":      ("record", "recording", str),
    "RETRACE_WORKSPACE_PATH": ("record", "workspace", str),
    "RETRACE_FILE_PATTERNS":  ("record", "retrace_file_patterns", str),
    "RETRACE_STALL_TIMEOUT":  ("writer", "stall_timeout", str),
    "RETRACE_INFLIGHT_LIMIT": ("writer", "inflight_limit", str),
    "RETRACE_QUEUE_CAPACITY": ("writer", "queue_capacity", str),
    "RETRACE_FLUSH_INTERVAL": ("writer", "flush_interval", str),
    "RETRACE_QUIT_ON_ERROR": ("record", "quit_on_error", bool),
}


def load_retrace_config(name_or_path=None):
    raw = name_or_path or os.environ.get("RETRACE_CONFIG", "release")

    if "/" in raw or "\\" in raw or raw.endswith(".toml"):
        with open(raw, "rb") as f:
            config = tomllib.load(f)
    else:
        ref = importlib.resources.files("retracesoftware").joinpath(f"{raw}.toml")
        try:
            data = ref.read_bytes()
        except (FileNotFoundError, TypeError):
            raise FileNotFoundError(f"No bundled retrace config preset: {raw}")
        config = tomllib.loads(data.decode("utf-8"))

    _apply_env_overrides(config)
    return config


def _apply_env_overrides(config):
    for env_key, (section, key, typ) in _ENV_OVERRIDES.items():
        val = os.environ.get(env_key)
        if val is None:
            continue
        config.setdefault(section, {})
        if typ is bool:
            config[section][key] = val.strip().lower() in ("1", "true", "yes", "on")
        else:
            config[section][key] = val


def config_to_argv(config):
    """Convert a resolved config dict into CLI args for retracesoftware.

    Quantity values (sizes, durations) are parsed here so __main__.py
    only ever receives plain numbers.
    """
    argv = []
    record = config.get("record", {})
    writer = config.get("writer", {})

    if "recording" in record:
        argv.extend(["--recording", str(record["recording"])])
    if record.get("verbose"):
        argv.append("--verbose")
    if record.get("stacktraces"):
        argv.append("--stacktraces")
    if record.get("trace_shutdown"):
        argv.append("--trace_shutdown")
    if record.get("trace_inputs"):
        argv.append("--trace_inputs")
    if record.get("quit_on_error"):
        argv.append("--quit_on_error")
    if "workspace" in record:
        argv.extend(["--workspace", str(record["workspace"])])
    if "retrace_file_patterns" in record:
        argv.extend(["--retrace_file_patterns", str(record["retrace_file_patterns"])])
    if "monitor" in record:
        argv.extend(["--monitor", str(record["monitor"])])

    _SIZE_KEYS = ("inflight_limit", "queue_capacity", "return_queue_capacity")
    _DURATION_KEYS = ("stall_timeout", "flush_interval")

    for key in _SIZE_KEYS:
        if key in writer:
            argv.extend([f"--{key}", str(parse_size(writer[key]))])

    for key in _DURATION_KEYS:
        if key in writer:
            argv.extend([f"--{key}", str(parse_duration(writer[key]))])

    return argv
