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
                "print(json.dumps({\n"
                "    'executable': sys.executable,\n"
                "    'module': retrace.__name__,\n"
                "    'has_coordinates': hasattr(retrace, 'coordinates'),\n"
                "    'has_call_at': hasattr(retrace, 'call_at'),\n"
                "    'has_thread_delta': hasattr(retrace, 'thread_delta'),\n"
                "    'has_callbacks': hasattr(retrace, 'callbacks'),\n"
                "}))\n"
            ),
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["module"] == "retrace"
    assert payload["executable"]
    assert payload["has_coordinates"]
    assert payload["has_call_at"]
    assert payload["has_thread_delta"]
    assert payload["has_callbacks"]
