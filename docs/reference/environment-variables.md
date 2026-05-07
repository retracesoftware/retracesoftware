# Environment Variables

Retrace environment variables are uppercase.

## User-Facing Variables

| Variable | Used For | Description |
|---|---|---|
| `RETRACE_RECORDING` | record | Recording path used by the `.pth` auto-enable hook |
| `RETRACE_CONFIG` | record | Bundled config preset name or path to a config file |
| `RETRACE_VERBOSE` | record | Enables verbose recording output when truthy |
| `RETRACE_STACKTRACES` | record | Captures stack traces when truthy |
| `RETRACE_TRACE_INPUTS` | record | Writes call parameters for debugging when truthy |
| `RETRACE_SHUTDOWN` | record | Traces shutdown and cleanup hooks when truthy |
| `RETRACE_GC_COLLECT_MULTIPLIER` | record | Configures deterministic GC collection checks |
| `RETRACE_SKIP_CHECKSUMS` | replay | Debug escape hatch for checksum/version validation |

The normal quickstart recording command is:

```
RETRACE_RECORDING=recordings/flask.retrace python examples/flask_demo.py
```

Lowercase names such as `retrace_recording` do not enable recording on macOS or
Linux.

## Auto-Enable Behavior

`python -m retracesoftware install` installs a `.pth` file. On fresh Python
startup, that hook imports `retracesoftware.autoenable`.

If `RETRACE_RECORDING` is set, the hook uses that value as the recording path,
prepares the `.retrace` file, and re-executes Python as:

```
python -m retracesoftware --recording <path> -- <original command>
```

If `RETRACE_CONFIG` is set, the hook loads that config preset or config file.
The bundled `release` and `debug` presets include:

```
recording = "{script}.retrace"
```

That means this also records:

```
RETRACE_CONFIG=debug python examples/flask_demo.py
```

If both are set, `RETRACE_RECORDING` overrides the recording path from the
config.

If neither `RETRACE_RECORDING` nor `RETRACE_CONFIG` is set, ordinary Python
startup continues.

## Debug Variables

| Variable | Description |
|---|---|
| `RETRACE_DEBUG=1` | Loads debug native extensions and enables debug assertions where available |
| `RETRACE_DEBUG_PROTOCOL=1` | Enables debugger protocol logging for the VS Code/replay control path |
| `RETRACE_GILWATCH=1` | Attempts to enable the GIL watch helper library |

Use debug variables when creating a bug report or diagnosing replay divergence.
