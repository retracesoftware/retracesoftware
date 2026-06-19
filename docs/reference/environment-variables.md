# Environment Variables

Retrace environment variables are uppercase. Lowercase names such as
`retrace_recording` do not enable recording on macOS or Linux.

## Primary User-Facing Variables

| Variable | Used For | Description |
|---|---|---|
| `RETRACE` | record | Enables the active-environment hook when truthy |
| `RETRACE_RECORDING` | record | Recording path used by launchers and the active-environment hook |
| `RETRACE_CONFIG` | record | Bundled config preset name or path to a config file |
| `RETRACE_AUTO_DEBUG` | record/debug | Enables recording in the active-environment hook and runs `retrace-ai-driver` automatically if the command exits nonzero |
| `RETRACE_AI_SERVER` | debug | Retrace AI service URL passed to `retrace-ai-driver`; defaults to `https://retrace-ai-service.retracesoftware.workers.dev` |
| `RETRACE_API_KEY` | debug | Bearer token for the Retrace AI service; if unset, the driver requests a free client token |
| `RETRACE_SKIP_CHECKSUMS` | replay | Debug escape hatch for checksum/version validation |

For one-shot recording, prefer `retracepython`:

```
retracepython --recording recordings/example.retrace app.py
```

## Active-Environment Hook Behavior

`python -m retracesoftware enable-hook` installs an env-gated `.pth` hook into
the active Python environment. The hook does not import Retrace unless
`RETRACE=1`, `RETRACE_AUTO_DEBUG=1`, `RETRACE_RECORDING`, or `RETRACE_CONFIG`
is set.

When active, the hook prepares the `.retrace` file and re-executes Python as:

```
python -m retracesoftware --recording <path> -- <original command>
```

If only `RETRACE=1` is set, the hook loads the default `release` config. If
`RETRACE_CONFIG` is set, the hook loads that config preset or config file.
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

If none of `RETRACE`, `RETRACE_AUTO_DEBUG`, `RETRACE_RECORDING`, or
`RETRACE_CONFIG` is set, ordinary Python startup continues.

## Auto-Debug On Failure

Set `RETRACE_AUTO_DEBUG=1` or `RETRACE_AUTO_DEBUG=true` to supervise the
recording command. In an enabled active-environment hook, this also records with
the default config even when `RETRACE` is not set. If the command exits nonzero,
Retrace preserves that exit code and runs `retrace-ai-driver` against the
recording with `--tool-executor dap`.

The driver starts the Retrace DAP server and drives it through the
`retrace-ai-service`/provider configuration supplied to the driver. The launcher
passes through driver-oriented variables such as `RETRACE_AI_DRIVER_COMMAND`,
`RETRACE_AI_DRIVER`, `RETRACE_AI_SERVER`, `RETRACE_API_KEY`,
`RETRACE_AI_MAX_TOOL_CALLS`, `RETRACE_AI_TIME_BUDGET`,
`RETRACE_AI_MAX_OUTPUT_TOKENS`, and `RETRACE_REPLAY_BIN`.

Unset `RETRACE_AUTO_DEBUG`, or set it to `0` or `false`, to keep the normal
exec-based launcher behavior.

When `RETRACE_AUTO_DEBUG` records with the default recording path and the command
exits successfully, Retrace deletes the trace file. If you pass `--recording` or
set `RETRACE_RECORDING`, Retrace keeps that explicit trace even on success.

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
| `RETRACE_RECORDING_INODE` | Internal guard that prevents launchers from re-preparing the same recording file |
| `RETRACE_NO_VENV_BOOTSTRAP` | Internal guard used to keep `retracepython` one-shot even when an active-environment hook is installed |
| `RETRACE_AUTO_DEBUG_SUPERVISED` | Internal guard that prevents child Python processes from launching nested auto-debug supervisors |

Do not set internal guard variables manually in normal workflows.
