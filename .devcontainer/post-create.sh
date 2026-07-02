#!/usr/bin/env bash
set -euo pipefail

cd "${WORKSPACE_FOLDER:-$(pwd)}"

git fetch --tags origin 2>/dev/null || true

pip install -e . --no-build-isolation

go build -C go -o "${PWD}/.retrace-replay-bin" ./cmd/replay

if [[ -f vscode/package-lock.json ]]; then
  (cd vscode && npm ci && npm run build)
fi

echo "Dev container ready."
echo "  Python package: editable install"
echo "  Replay binary:  ${PWD}/.retrace-replay-bin"
echo "  Run tests:      python -m pytest tests/ -v --tb=short"
echo "  Go tests:       go test ./...  (from go/)"
