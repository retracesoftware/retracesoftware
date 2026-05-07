# CLI Reference

The main CLI entrypoint is:

```
python -m retracesoftware
```

There is also a `replay` console script installed by the package.

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

## Record Explicitly

Record without relying on the `.pth` hook:

```
python -m retracesoftware --recording recordings/example.retrace -- your_script.py
```

Everything after `--` is the target command.

Useful record flags:

| Flag | Description |
|---|---|
| `--recording PATH` | Output `.retrace` path |
| `--verbose` | Print detailed trace writer output |
| `--stacktraces` | Capture stack traces for recorded events |
| `--trace_inputs` | Write call parameters for debugging |
| `--trace_shutdown` | Trace shutdown and cleanup hooks |
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
| `--skip_weakref_callbacks` | Disable retrace inside weakref callbacks during replay |
| `--control_socket PATH` | Connect to the debugger control socket |
| `--stdio` | Read control commands from stdin and write responses to stdout |

## Replay Console Script

The package installs:

```
replay
```

This uses the same replay binary discovery path as `python -m retracesoftware`.
