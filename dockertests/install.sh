#!/bin/bash
# Auto-install dependencies to /app/packages volume
#
# Installs:
# 1. /app/test/requirements.txt (test-specific deps, if present)
# 2. /app/dockertests/base-requirements.txt (shared harness deps, if any)
# 3. /app/repo (the local retracesoftware checkout, when mounted)
#
# The compose harness then runs with PYTHONPATH=/app/packages.

set -e

TARGET="/app/packages"

export PIP_RETRIES="${PIP_RETRIES:-10}"
export PIP_DEFAULT_TIMEOUT="${PIP_DEFAULT_TIMEOUT:-60}"

mkdir -p "$TARGET"

# Ensure we don't carry stale wheels/modules across installs.
if [ "${RETRACE_CLEAN_PACKAGES:-1}" = "1" ]; then
    echo "[install.sh] Cleaning target directory: $TARGET"
    shopt -s dotglob nullglob
    rm -rf "$TARGET"/*
    shopt -u dotglob nullglob
fi

# Install test-specific requirements first so later harness installs restore
# /app/packages/bin/replay if a dependency recreates the script directory.
if [ -f "/app/test/requirements.txt" ]; then
    echo "[install.sh] Installing test-specific requirements to $TARGET..."
    pip install --no-cache-dir --upgrade --target "$TARGET" -r /app/test/requirements.txt
fi

# Install base requirements (common deps for the docker harness).
if [ -f "/app/dockertests/base-requirements.txt" ]; then
    echo "[install.sh] Installing base requirements to $TARGET..."
    pip install --no-cache-dir --upgrade --target "$TARGET" -r /app/dockertests/base-requirements.txt
fi

# Install the current checkout, not the latest published retracesoftware wheel.
# meson-python creates temporary build directories inside the source tree, so
# /app/repo is mounted writable by the compose harness.
if [ -f "/app/repo/pyproject.toml" ]; then
    if ! command -v c++ >/dev/null 2>&1; then
        echo "[install.sh] Installing native build toolchain..."
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends g++ >/dev/null
        rm -rf /var/lib/apt/lists/*
    fi

    echo "[install.sh] Installing local retracesoftware checkout to $TARGET..."
    SETUPTOOLS_SCM_PRETEND_VERSION="${SETUPTOOLS_SCM_PRETEND_VERSION:-0.0.0}" \
        pip install --no-cache-dir --upgrade --target "$TARGET" /app/repo
fi

echo "[install.sh] Dependencies installed to $TARGET"
