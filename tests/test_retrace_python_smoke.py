import json
import subprocess

from tests.helpers import PYTHON


def test_retrace_python_launches_patched_runtime():
    proc = subprocess.run(
        [
            PYTHON,
            "-c",
            (
                "import json, sys, retrace\n"
                "print(json.dumps({'executable': sys.executable, 'module': retrace.__name__}))\n"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["module"] == "retrace"
    assert "retracesoftware_cpython" in payload["executable"]
    assert "_runtime" in payload["executable"]
