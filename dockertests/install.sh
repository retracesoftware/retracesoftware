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
export NINJAFLAGS="${NINJAFLAGS:--j2}"
export RETRACE_GO_VERSION="${RETRACE_GO_VERSION:-1.25.0}"

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

go_is_new_enough() {
    python - "$1" <<'PY'
import re
import sys

version = sys.argv[1]
match = re.search(r"go(\d+)\.(\d+)", version)
if not match:
    sys.exit(1)
major, minor = map(int, match.groups())
sys.exit(0 if (major, minor) >= (1, 25) else 1)
PY
}

install_go_toolchain() {
    current="$(go env GOVERSION 2>/dev/null || true)"
    if [ -n "$current" ] && go_is_new_enough "$current"; then
        return
    fi

    arch="$(dpkg --print-architecture)"
    case "$arch" in
        amd64) go_arch="amd64" ;;
        arm64) go_arch="arm64" ;;
        *)
            echo "[install.sh] Unsupported architecture for Go install: $arch" >&2
            exit 1
            ;;
    esac

    echo "[install.sh] Installing Go ${RETRACE_GO_VERSION} for linux-${go_arch}..."
    APT_PACKAGES=()
    if ! command -v wget >/dev/null 2>&1; then
        APT_PACKAGES+=(wget)
    fi
    if [ ! -d /etc/ssl/certs ]; then
        APT_PACKAGES+=(ca-certificates)
    fi
    if [ "${#APT_PACKAGES[@]}" -gt 0 ]; then
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
            "${APT_PACKAGES[@]}" >/dev/null
    fi
    rm -rf /usr/local/go
    wget -q "https://go.dev/dl/go${RETRACE_GO_VERSION}.linux-${go_arch}.tar.gz" -O /tmp/go.tgz
    tar -C /usr/local -xzf /tmp/go.tgz
    rm -f /tmp/go.tgz
    export PATH="/usr/local/go/bin:$PATH"
}

# Install the current checkout, not the latest published retracesoftware wheel.
# meson-python creates temporary build directories inside the source tree, so
# /app/repo is mounted writable by the compose harness.
if [ -f "/app/repo/pyproject.toml" ]; then
    BUILD_PACKAGES=()
    if ! command -v c++ >/dev/null 2>&1; then
        BUILD_PACKAGES+=(g++)
    fi
    if [ "${#BUILD_PACKAGES[@]}" -gt 0 ]; then
        echo "[install.sh] Installing build tools: ${BUILD_PACKAGES[*]}"
        apt-get update -qq
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends "${BUILD_PACKAGES[@]}" >/dev/null
        rm -rf /var/lib/apt/lists/*
    fi

    install_go_toolchain

    echo "[install.sh] Installing local retracesoftware checkout to $TARGET..."
    PIP_BUILD_OPTIONS=()
    if command -v ninja >/dev/null 2>&1 && python -c "import mesonbuild, mesonpy, setuptools_scm" >/dev/null 2>&1; then
        echo "[install.sh] Using existing Python build backend without build isolation"
        PIP_BUILD_OPTIONS+=(--no-build-isolation)
    fi

    SETUPTOOLS_SCM_PRETEND_VERSION="${SETUPTOOLS_SCM_PRETEND_VERSION:-0.0.0}" \
        pip install --no-cache-dir --upgrade "${PIP_BUILD_OPTIONS[@]}" --target "$TARGET" /app/repo
fi

echo "[install.sh] Dependencies installed to $TARGET"
