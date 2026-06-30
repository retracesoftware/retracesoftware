# appnope pth auto-enable replay regression

This is the manual end-to-end reproducer for the appnope/multiprocessing
failure that only appears with the public `.pth` auto-enable recording flow.

Run from this directory on macOS:

```bash
cd /path/to/retracesoftware/dockertests/tests/appnope_pth_autoenable_test
[ -f requirements.txt ] && python -m pip install -q -r requirements.txt
python -m retracesoftware enable-hook

rm -f test.retrace
rm -rf test.d

RETRACE_RECORDING=test.retrace RETRACE_CONFIG=debug python test.py

./test.retrace --extract

ROOT_PID=$(python -m retracesoftware --recording test.retrace --list_pids | head -1)
echo "ROOT_PID=$ROOT_PID"

./test.d/${ROOT_PID}.bin
```

Current failing replay signature:

```text
RuntimeError: Could not read: 1 bytes from tracefile with timeout: 1000 milliseconds
...
RuntimeError: failed to patch _io.open
replay: replay exited: exit status 1
```

The related direct-wrapper flow is expected to pass for the same app body:

```bash
RETRACE_CONFIG=debug python -m retracesoftware --recording test.retrace -- test.py
```
