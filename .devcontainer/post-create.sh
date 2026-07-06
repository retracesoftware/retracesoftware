#!/usr/bin/env bash
set -euo pipefail

cd "${WORKSPACE_FOLDER:-$(pwd)}"

git fetch --tags origin 2>/dev/null || true

pip install -e . --no-build-isolation

_retrace_dap_src="${RETRACE_DAP_SRC:-$(cd .. && pwd)/retrace-dap}"
if [[ -f "${_retrace_dap_src}/cmd/replay/main.go" ]]; then
  go build -C "${_retrace_dap_src}" -o "${PWD}/.retrace-replay-bin" ./cmd/replay
else
  echo "retrace-dap source not found at ${_retrace_dap_src}" >&2
  echo "Clone github.com/retracesoftware/retrace-dap beside this repo or set RETRACE_DAP_SRC." >&2
fi

if [[ -f vscode/package-lock.json ]]; then
  (cd vscode && npm ci && npm run build)
fi

echo "Dev container ready."
echo "  Python package: editable install"
echo "  Replay binary:  ${PWD}/.retrace-replay-bin"
echo "  Run tests:      python -m pytest tests/ -v --tb=short"
