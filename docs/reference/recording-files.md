# Recording Files

Retrace creates `.retrace` recording files and extracted per-process replay
files.

## `.retrace`

When you run:

```
RETRACE_RECORDING=recordings/flask.retrace python examples/flask_demo.py
```

Retrace creates:

```
recordings/flask.retrace
```

The name is just a path. You can choose another name:

```
RETRACE_RECORDING=recordings/my-debug-run.retrace python examples/flask_demo.py
```

If the parent directory does not exist, Retrace creates it.

The `.retrace` file is executable. It contains a shebang that points at the
replay binary used for extraction and replay tooling.

## Extracted Directory

Extract a recording:

```
./recordings/flask.retrace --extract
```

For `recordings/flask.retrace`, extraction creates:

```
recordings/flask.d/
```

Inside that directory:

```
recordings/flask.d/index.json
recordings/flask.d/<PID>.bin
```

`index.json` describes the recorded process tree. Each `<PID>.bin` file is a
PidFile for one recorded process.

## PidFile Replay

Find the root process id:

```
ROOT_PID=$(python -m retracesoftware --recording recordings/flask.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"
```

Replay it:

```
./recordings/flask.d/${ROOT_PID}.bin
```

## Git Hygiene

Recordings and extracted PidFiles are generated artifacts. Do not commit them
unless you are intentionally adding a small fixture.

For demos, use a `.gitignore` like:

```
/recordings/*.retrace
/recordings/*.d/
/recordings/*.log
```
