#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
RETRACE_INSTALL_TARGET="${RETRACE_INSTALL_TARGET:-retracesoftware==0.2.3}"

cd "$(dirname "$0")"

"$PYTHON_BIN" -m venv .venv

if [ -n "${RETRACE_PIP_ARGS:-}" ]; then
  # Intentionally allow callers to pass multiple pip flags.
  # shellcheck disable=SC2086
  .venv/bin/python -m pip install --upgrade --no-cache-dir $RETRACE_PIP_ARGS "$RETRACE_INSTALL_TARGET"
else
  .venv/bin/python -m pip install --upgrade --no-cache-dir "$RETRACE_INSTALL_TARGET"
fi

.venv/bin/python -m retracesoftware --recording medium.retrace --stacktraces -- main.py

case_dir="$(pwd)"
cat > medium.code-workspace <<JSON
{
  "folders": [
    {
      "path": "$case_dir"
    }
  ],
  "settings": {
    "retrace.recording": "$case_dir/medium.retrace"
  },
  "launch": {
    "version": "0.2.0",
    "configurations": [
      {
        "type": "retrace",
        "request": "launch",
        "name": "Retrace Medium Repro",
        "recording": "$case_dir/medium.retrace"
      }
    ]
  }
}
JSON

echo
echo "Recorded $case_dir/medium.retrace"
echo "Open $case_dir/medium.code-workspace in VS Code."
