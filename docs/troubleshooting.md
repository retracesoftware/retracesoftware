# Troubleshooting

Start with the smallest check that matches your symptom.

## Replay Binary Missing

Supported PyPI wheels include Retrace's replay binary. If replay tooling reports
that the binary is missing, first confirm you installed a wheel for a supported
platform rather than building from source:

```
python -m pip show retracesoftware
python -c "from retracesoftware.replay import binary_path; print(binary_path())"
```

If you are installing from source or on an unsupported platform where `pip` has
to build from source, install Go 1.25 or newer before rebuilding.

On macOS with Homebrew:

```
brew install go
```

On Linux, install Go 1.25 or newer from your distro packages or from
[go.dev/dl](https://go.dev/dl/).

## Recording Did Not Start

Confirm Retrace is installed in the active environment:

```
python -m pip show retracesoftware
```

If you are using the auto-enable workflow, confirm the hook is installed:

```
python -m retracesoftware install
```

Then record with uppercase `RETRACE_RECORDING`:

```
RETRACE_RECORDING=recordings/test.retrace python your_script.py
```

Do not use lowercase `retrace_recording`.

## `.retrace` File Exists But Replay Fails

First extract and replay in the terminal:

```
./recordings/test.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording recordings/test.retrace --list_pids | head -1)
./recordings/test.d/${ROOT_PID}.bin
```

If terminal replay fails, debug that before trying VS Code.

## Python Version Mismatch

Use the same virtual environment for recording and replay. Retrace validates
the recorded interpreter during replay.

Check your current interpreter:

```
python --version
```

## Replay Diverges

A divergence means replay did not follow the same boundary-call sequence as the
recording.

Re-record with diagnostics:

```
RETRACE_DEBUG=1 python -m retracesoftware \
  --recording recordings/debug.retrace \
  --verbose \
  --stacktraces \
  -- your_script.py
```

Then extract and replay:

```
./recordings/debug.retrace --extract
ROOT_PID=$(python -m retracesoftware --recording recordings/debug.retrace --list_pids | head -1)
./recordings/debug.d/${ROOT_PID}.bin
```

Common causes:

- a nondeterministic function is not intercepted
- replay touches live filesystem, network, clock, RNG, or other external state
- thread scheduling or finalizer timing exposes a missing recorded boundary
- record and replay are using different Python versions or package builds

For maintainer-level debugging, see [Debugging Retrace](DEBUGGING.md).

## VS Code Does Not Hit Breakpoints

Check terminal replay first.

Then confirm:

- the Retrace Debug Extension is installed
- you opened the same folder that contains the source shown in the recording
- you opened the `.retrace` file through the Retrace sidebar or context menu
- the breakpoint is in code that actually executes during the recorded run

If VS Code still does not stop, enable protocol logging in VS Code settings:

```
retrace.debugProtocol = true
```

## Permission Denied Running A Recording

The `.retrace` file should be executable. If it is not:

```
chmod +x recordings/test.retrace
```

Then try extraction again:

```
./recordings/test.retrace --extract
```

## Clean Generated Files

```
rm -f recordings/*.retrace
rm -rf recordings/*.d
```
