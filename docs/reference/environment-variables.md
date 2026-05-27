# Environment Variables

Retrace environment variables are uppercase. Lowercase names such as
`retrace_recording` do not enable recording on macOS or Linux.

## Primary User-Facing Variables

| Variable | Used For | Description |
|---|---|---|
| `RETRACE_RECORDING` | record | Recording path used by the `.pth` auto-enable hook |
| `RETRACE_CONFIG` | record | Bundled config preset name or path to a config file |
| `RETRACE_SKIP_CHECKSUMS` | replay | Debug escape hatch for checksum/version validation |

For commands that use the auto-enable hook, a typical recording command is:

```
RETRACE_RECORDING=recordings/example.retrace python app.py
```

## Auto-Enable Behavior

`python -m retracesoftware install` installs a `.pth` file into the active
Python environment. On fresh Python startup, that hook imports
`retracesoftware.autoenable`.

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
RETRACE_CONFIG=debug python app.py
```

If both are set, `RETRACE_RECORDING` overrides the recording path from the
config.

If neither `RETRACE_RECORDING` nor `RETRACE_CONFIG` is set, ordinary Python
startup continues.

## Recording And Config Overrides

These variables are read by the recording config path and correspond to CLI
record flags.

| Variable | Description |
|---|---|
| `RETRACE_VERBOSE` | Enables verbose recording output when truthy |
| `RETRACE_STACKTRACES` | Captures stack traces when truthy |
| `RETRACE_TRACE_INPUTS` | Writes call parameters for debugging when truthy |
| `RETRACE_SHUTDOWN` | Traces shutdown and cleanup hooks when truthy |
| `RETRACE_GC_COLLECT_MULTIPLIER` | Configures deterministic GC collection checks |
| `RETRACE_FORMAT` | Overrides the recording stream format |
| `RETRACE_FILE_PATTERNS` | Path to extra file-pattern rules |
| `RETRACE_INFLIGHT_LIMIT` | Maximum in-flight writer buffer size |
| `RETRACE_QUEUE_CAPACITY` | Writer queue capacity |
| `RETRACE_CONSUMER_WAIT_TIMEOUT_MS` | Writer consumer wait timeout |
| `RETRACE_FLUSH_INTERVAL` | Periodic writer flush interval |
| `RETRACE_QUIT_ON_ERROR` | Stops after writer/proxy errors instead of continuing |

The default public workflow uses the standard `.retrace` recording container
and extracted PidFiles. Format overrides are for development and debugging.

## Debug Variables

| Variable | Description |
|---|---|
| `RETRACE_DEBUG=1` | Loads debug native extensions and enables debug assertions where available |
| `RETRACE_DEBUG_PROTOCOL=1` | Enables debugger protocol logging for the VS Code/replay control path |
| `RETRACE_GILWATCH=1` | Attempts to enable the GIL watch helper library |

Use debug variables when creating a bug report or diagnosing replay divergence.

## Module And Replay Binary Overrides

| Variable | Description |
|---|---|
| `RETRACE_MODULES_PATH` | Additional module interception config search path |
| `RETRACE_REPLAY_BIN` | Absolute path to the Go replay binary used by replay discovery |
| `RETRACE_REPLAY_SRC` | Source checkout used when lazily building the Go replay binary |
| `REPLAY_BIN` | Lower-level replay binary override used by tape/replay discovery paths |

Most users should not need these. They are useful in source checkouts,
editable installs, and local packaging tests.

## Internal Guard Variables

| Variable | Description |
|---|---|
| `RETRACE_INODE` | Internal auto-enable guard that prevents recursive re-exec of the same Python process |

Do not set internal guard variables manually in normal workflows.
