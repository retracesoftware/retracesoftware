#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
RETRACE_INSTALL_TARGET="${RETRACE_INSTALL_TARGET:-retracesoftware==0.2.3}"

cd "$(dirname "$0")"

"$PYTHON_BIN" -m venv .venv

if [ -n "${RETRACE_PIP_ARGS:-}" ]; then
  # Intentionally allow callers to pass multiple pip flags.
  # shellcheck disable=SC2086
  .venv/bin/python -m pip install --upgrade --no-cache-dir $RETRACE_PIP_ARGS "$RETRACE_INSTALL_TARGET" flask requests
else
  .venv/bin/python -m pip install --upgrade --no-cache-dir "$RETRACE_INSTALL_TARGET" flask requests
fi

echo "Plain run:"
.venv/bin/python run_flask_live.py

echo
echo "Recording:"
.venv/bin/python -m retracesoftware --recording flask-live.retrace --stacktraces -- run_flask_live.py

echo
echo "Recorded $(pwd)/flask-live.retrace"
