#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
RETRACE_INSTALL_TARGET="${RETRACE_INSTALL_TARGET:-retracesoftware==0.2.3}"

cd "$(dirname "$0")"

"$PYTHON_BIN" -m venv .venv

if [ -n "${RETRACE_PIP_ARGS:-}" ]; then
  # Intentionally allow callers to pass multiple pip flags.
  # shellcheck disable=SC2086
  .venv/bin/python -m pip install --upgrade --no-cache-dir $RETRACE_PIP_ARGS "$RETRACE_INSTALL_TARGET" flask
else
  .venv/bin/python -m pip install --upgrade --no-cache-dir "$RETRACE_INSTALL_TARGET" flask
fi

.venv/bin/python -m retracesoftware --recording flask-testclient.retrace --stacktraces -- run_flask_testclient.py

case_dir="$(pwd)"
cat > flask-testclient.code-workspace <<JSON
{
  "folders": [
    {
      "path": "$case_dir"
    }
  ],
  "settings": {
    "retrace.recording": "$case_dir/flask-testclient.retrace"
  },
  "launch": {
    "version": "0.2.0",
    "configurations": [
      {
        "type": "retrace",
        "request": "launch",
        "name": "Retrace Flask TestClient Repro",
        "recording": "$case_dir/flask-testclient.retrace"
      }
    ]
  }
}
JSON

echo
echo "Recorded $case_dir/flask-testclient.retrace"
echo "Open $case_dir/flask-testclient.code-workspace in VS Code."
