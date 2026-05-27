# Recording Files

Retrace creates `.retrace` recording files and extracted per-process replay
files.

## `.retrace`

When you run:

```
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m retracesoftware --recording recordings/pytest.retrace -- -m pytest pytest_demo -q --tb=short
```

Retrace creates:

```
recordings/pytest.retrace
```

The name is just a path. You can choose another name:

```
python -m retracesoftware --recording recordings/my-debug-run.retrace -- app.py
```

If the parent directory does not exist, Retrace creates it.

The `.retrace` file is executable. It contains a shebang that points at the
replay binary used for extraction and replay tooling.

## Extracted Directory

Extract a recording:

```
./recordings/pytest.retrace --extract
```

For `recordings/pytest.retrace`, extraction creates:

```
recordings/pytest.d/
```

Inside that directory:

```
recordings/pytest.d/index.json
recordings/pytest.d/<PID>.bin
```

`index.json` describes the recorded process tree. Each `<PID>.bin` file is a
PidFile for one recorded process.

## PidFile Replay

Find the root process id:

```
ROOT_PID=$(python -m retracesoftware --recording recordings/pytest.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"
```

Replay it:

```
./recordings/pytest.d/${ROOT_PID}.bin
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
