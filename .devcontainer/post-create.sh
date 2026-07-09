#!/usr/bin/env bash
set -euo pipefail

cd "${WORKSPACE_FOLDER:-$(pwd)}"

git fetch --tags origin 2>/dev/null || true

pip install -e . --no-build-isolation

python - <<'PY'
from retracesoftware.replay import binary_path

print(f"Replay binary: {binary_path()}")
PY

if [[ -f vscode/package-lock.json ]]; then
  (cd vscode && npm ci && npm run build)
fi

echo "Dev container ready."
echo "  Python package: editable install"
echo "  Replay binary:  packaged retracesoftware-dap binary"
echo "  Run tests:      python -m pytest tests/ -v --tb=short"
