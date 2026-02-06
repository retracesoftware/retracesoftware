#!/bin/bash
# Auto-install dependencies to /app/packages volume
#
# Installs:
# 1. /app/dockertests/base-requirements.txt (common deps like retracesoftware_utils)
# 2. /app/test/requirements.txt (test-specific deps, if present)
#
# Note: retracesoftware itself is mounted via PYTHONPATH=/app/src:/app/packages

set -e

TARGET="/app/packages"

# Install base requirements (common deps for retrace)
if [ -f "/app/dockertests/base-requirements.txt" ]; then
    echo "[install.sh] Installing base requirements to $TARGET..."
    pip install --target "$TARGET" -r /app/dockertests/base-requirements.txt
fi

# Install test-specific requirements if present
if [ -f "/app/test/requirements.txt" ]; then
    echo "[install.sh] Installing test-specific requirements to $TARGET..."
    pip install --target "$TARGET" -r /app/test/requirements.txt
fi

echo "[install.sh] Dependencies installed to $TARGET"
