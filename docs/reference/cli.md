# CLI Reference

The main CLI entrypoint is:

```
python -m retracesoftware
```

There is also a `replay` console script installed by the package.

Run `python -m retracesoftware --help` to inspect the top-level command. Record
flags are parsed when the invocation contains a target command after `--`.

## Install And Uninstall Auto-Enable

Install the `.pth` auto-enable hook into the active Python environment:

```
python -m retracesoftware install
```

Remove it:

```
python -m retracesoftware uninstall
```

## Record With Auto-Enable

After `python -m retracesoftware install`, run ordinary Python with
`RETRACE_RECORDING`:

```
RETRACE_RECORDING=recordings/example.retrace python your_script.py
```

Module commands work the same way:

```
RETRACE_RECORDING=recordings/example.retrace python -m your_package.cli arg1 arg2
```

## Record Explicitly

Record without relying on the `.pth` hook:

```
python -m retracesoftware --recording recordings/example.retrace -- your_script.py
```

Everything after `--` is the target command.

For module commands, include `-m` after `--`:

```
python -m retracesoftware --recording recordings/example.retrace -- -m your_package.cli arg1 arg2
```

Useful record flags:

| Flag | Description |
|---|---|
| `--recording PATH` | Output `.retrace` path |
| `--verbose` | Print detailed trace writer output |
| `--stacktraces` | Capture stack traces for recorded events |
| `--trace_inputs` | Write call parameters for debugging |
| `--trace_shutdown` | Trace shutdown and cleanup hooks |
| `--inflight_limit BYTES` | Maximum in-flight writer buffer size |
| `--queue_capacity N` | Writer queue capacity |
| `--consumer_wait_timeout_ms N` | Writer consumer wait timeout |
| `--flush_interval SECONDS` | Periodic writer flush interval |
| `--quit_on_error` | Stop after writer/proxy errors instead of continuing |
| `--format FORMAT` | Recording stream format override for development/debugging |
| `--replay_bin PATH` | Replay binary path recorded into the `.retrace` shebang |
| `--retrace_file_patterns PATH` | Extra file-pattern config used by path-based interception |
| `--monitor N` | Enable monitoring diagnostics on Python 3.12+ |
| `--gc_collect_multiplier N` | Trigger replayable GC collection at intercepted safe points |

Example with diagnostics:

```
RETRACE_DEBUG=1 python -m retracesoftware \
  --recording recordings/debug.retrace \
  --verbose \
  --stacktraces \
  -- your_script.py
```

## Extract A Recording

A `.retrace` file is executable:

```
./recordings/example.retrace --extract
```

This creates a sibling `.d` directory:

```
recordings/example.d/
```

## List Recorded Processes

```
python -m retracesoftware --recording recordings/example.retrace --list_pids
```

## Replay A Process

After extraction:

```
ROOT_PID=$(python -m retracesoftware --recording recordings/example.retrace --list_pids | head -1)
./recordings/example.d/${ROOT_PID}.bin
```

Useful replay flags:

| Flag | Description |
|---|---|
| `--recording PATH` | Recording or PidFile path to replay |
| `--list_pids` | Print recorded process ids |
| `--read_timeout N` | Milliseconds to wait for incomplete reads |
| `--format FORMAT` | Override input stream format for debugging |
| `--chunk_ms N` | Replay chunking interval used by cursor/debug flows |
| `--skip_weakref_callbacks` | Disable retrace inside weakref callbacks during replay |
| `--control_socket PATH` | Connect to the debugger control socket |
| `--stdio` | Read control commands from stdin and write responses to stdout |

## Replay Console Script

The package installs:

```
replay
```

This uses the same replay binary discovery path as `python -m retracesoftware`.

The `replay` script is the Go replay tool. It is also written into the shebang
of `.retrace` files, which is why executable recordings can be run directly.

Common recording commands:

```
replay --recording recordings/example.retrace --index
replay --recording recordings/example.retrace --extract
replay --recording recordings/example.retrace --workspace
replay --recording recordings/example.retrace --dap
```

Common PidFile commands:

```
replay recordings/example.d/12345.bin
replay --dap recordings/example.d/12345.bin
```

An executable recording uses the same tool:

```
./recordings/example.retrace --extract
```

For quick command-line validation, extracting and replaying the root PidFile is
usually enough. For interactive debugging, open the `.retrace` file in VS Code
with the Retrace extension installed.
