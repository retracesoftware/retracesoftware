#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON_BIN:-python3.12}"
RETRACE_INSTALL_TARGET="${RETRACE_INSTALL_TARGET:-retracesoftware}"

cd "$(dirname "$0")"

"$PYTHON_BIN" -m venv .venv

if [ -n "${RETRACE_PIP_ARGS:-}" ]; then
  # Intentionally allow callers to pass multiple pip flags.
  # shellcheck disable=SC2086
  .venv/bin/python -m pip install --upgrade --no-cache-dir $RETRACE_PIP_ARGS "$RETRACE_INSTALL_TARGET"
else
  .venv/bin/python -m pip install --upgrade --no-cache-dir "$RETRACE_INSTALL_TARGET"
fi

echo "Plain run:"
.venv/bin/python run_subprocess_env_capture_minimal.py

echo
echo "Recording:"
.venv/bin/python -m retracesoftware --recording subprocess-env-capture.retrace --stacktraces -- run_subprocess_env_capture_minimal.py

echo
echo "Index:"
.venv/bin/replay --recording subprocess-env-capture.retrace --index

echo
echo "Extracting:"
.venv/bin/replay --recording subprocess-env-capture.retrace --extract

pidfile="$(.venv/bin/python - <<'PY'
import json

with open("subprocess-env-capture.d/index.json", "r", encoding="utf-8") as handle:
    index = json.load(handle)

print(f"subprocess-env-capture.d/{index['root']['pid']}.bin")
PY
)"

echo
echo "Replaying ${pidfile}:"
.venv/bin/replay "$pidfile"
