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
# Build from a container-local source copy. Docker Desktop bind mounts can give
# Meson just-created files timestamps a few milliseconds in the future, which
# makes Meson abort with a clock-skew error during `pip install`.
if [ -f "/app/repo/pyproject.toml" ]; then
    if ! command -v c++ >/dev/null 2>&1; then
        echo "[install.sh] Installing native build toolchain..."
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends g++ >/dev/null
        rm -rf /var/lib/apt/lists/*
    fi

    echo "[install.sh] Preparing local retracesoftware checkout for install..."
    BUILD_SRC="$(mktemp -d /tmp/retracesoftware-build-src.XXXXXX)"
    cleanup_build_src() {
        rm -rf "$BUILD_SRC"
    }
    trap cleanup_build_src EXIT

    tar \
        --exclude='.git' \
        --exclude='.mypy_cache' \
        --exclude='.pytest_cache' \
        --exclude='.ruff_cache' \
        --exclude='.venv*' \
        --exclude='.mesonpy-*' \
        --exclude='*.egg-info' \
        --exclude='build' \
        --exclude='builddir' \
        --exclude='dist' \
        --exclude='dockertests/.cache' \
        --exclude='dockertests/tests/*/recording' \
        --exclude='dockertests/tests/*/test.d' \
        --exclude='dockertests/tests/*/test.retrace' \
        --exclude='retrace' \
        --exclude='retrace-cpython' \
        -C /app/repo -cf - . | tar -C "$BUILD_SRC" -xf -

    PIP_BUILD_OPTIONS=()
    if command -v ninja >/dev/null 2>&1 && python -c "import mesonbuild, mesonpy, setuptools_scm" >/dev/null 2>&1; then
        echo "[install.sh] Using existing Python build backend without build isolation"
        PIP_BUILD_OPTIONS+=(--no-build-isolation)
    fi

    echo "[install.sh] Installing local retracesoftware checkout to $TARGET..."
    SETUPTOOLS_SCM_PRETEND_VERSION="${SETUPTOOLS_SCM_PRETEND_VERSION:-0.0.0}" \
        pip install --no-cache-dir --upgrade "${PIP_BUILD_OPTIONS[@]}" --target "$TARGET" "$BUILD_SRC"
fi

echo "[install.sh] Dependencies installed to $TARGET"
