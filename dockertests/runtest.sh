#!/bin/bash
# Run a retrace test
#
# Usage:
#   ./runtest.sh <test_name> [options]
#
# Options:
#   --image <image>   Docker image (default: python:3.11-slim)
#   --debug           Run record under GDB, drop to console on crash
#
# Examples:
#   ./runtest.sh postgress_test
#   ./runtest.sh time1 --image python:3.11-slim
#   ./runtest.sh time1 --debug

set -e

# Parse arguments
DEBUG_MODE=false
TEST_IMAGE="python:3.11-slim"
TEST_NAME=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --debug)
            DEBUG_MODE=true
            shift
            ;;
        --image)
            TEST_IMAGE="$2"
            shift 2
            ;;
        -*)
            echo "Unknown option: $1"
            exit 1
            ;;
        *)
            if [ -z "$TEST_NAME" ]; then
                TEST_NAME="$1"
            fi
            shift
            ;;
    esac
done

if [ -z "$TEST_NAME" ]; then
    echo "Usage: ./runtest.sh <test_name> [--image <image>] [--debug]"
    exit 1
fi

TEST_DIR="./tests/${TEST_NAME}"

# Check test directory exists
if [ ! -d "$TEST_DIR" ]; then
    echo "❌ Test directory not found: $TEST_DIR"
    exit 1
fi

# Check for test.py
if [ ! -f "$TEST_DIR/test.py" ]; then
    echo "❌ No test.py found in $TEST_DIR"
    exit 1
fi

# Detect test type: server (has client.py) or script (default)
if [ -f "$TEST_DIR/client.py" ]; then
    BASE_COMPOSE="docker-compose.server-base.yml"
    TEST_TYPE="server"
    PHASE_SERVICES=("install" "server-dryrun" "dryrun" "server-record" "record" "replay" "cleanup")
else
    BASE_COMPOSE="docker-compose.base.yml"
    TEST_TYPE="script"
    PHASE_SERVICES=("install" "dryrun" "record" "replay" "cleanup")
fi

echo "🧪 Running test: $TEST_NAME ($TEST_TYPE)"
echo "   Image: $TEST_IMAGE"
if [ "$DEBUG_MODE" = true ]; then
    echo "   Mode: DEBUG (GDB)"
fi
echo ""

# Isolate installed packages by test name and image to avoid cross-test contamination.
SAFE_IMAGE_TAG="$(echo "$TEST_IMAGE" | tr '/:@' '___' | tr -c '[:alnum:]_.-' '_')"
SAFE_TEST_NAME="$(echo "$TEST_NAME" | tr -c '[:alnum:]_.-' '_')"
TEST_PACKAGES_DIR="./.cache/packages/${TEST_NAME}/${SAFE_IMAGE_TAG}"
TEST_PACKAGES_DEBUG_DIR="./.cache/packages-debug/${TEST_NAME}/${SAFE_IMAGE_TAG}"
COMPOSE_PROJECT_NAME="retracetest_${SAFE_TEST_NAME}_$(date +%s)_$$"
mkdir -p "$TEST_PACKAGES_DIR" "$TEST_PACKAGES_DEBUG_DIR"

# Export variables
export TEST_IMAGE
export TEST_DIR
export TEST_PACKAGES_DIR
export COMPOSE_PROJECT_NAME

# Build compose command
COMPOSE_CMD="docker compose --progress plain -p $COMPOSE_PROJECT_NAME -f $BASE_COMPOSE"
if [ -f "$TEST_DIR/docker-compose.yml" ]; then
    COMPOSE_CMD="$COMPOSE_CMD -f $TEST_DIR/docker-compose.yml"
fi

cleanup_compose() {
    set +e
    $COMPOSE_CMD down --remove-orphans >/dev/null 2>&1
    local cleanup_exit_code=$?
    set -e
    if [ "$cleanup_exit_code" -ne 0 ]; then
        echo "⚠️  Warning: docker compose cleanup failed for project: $COMPOSE_PROJECT_NAME"
    fi
}

detect_failed_phase() {
    local compose_output
    compose_output="$($COMPOSE_CMD ps -a --format json 2>/dev/null || true)"
    if [ -z "$compose_output" ]; then
        echo ""
        return
    fi

    local phases_csv
    phases_csv="$(IFS=,; echo "${PHASE_SERVICES[*]}")"

    # Parse docker compose JSON output to find the earliest failed phase service.
    # Supports both JSON-lines and JSON-array output formats from different docker compose versions.
    printf '%s\n' "$compose_output" | PHASE_SERVICES_CSV="$phases_csv" python -c '
import json
import os
import sys

phase_order = [p for p in os.environ.get("PHASE_SERVICES_CSV", "").split(",") if p]
status_by_service = {}

raw = sys.stdin.read().strip()
if not raw:
    print("")
    sys.exit(0)

def feed(item):
    service = item.get("Service")
    if not service:
        return
    exit_code = item.get("ExitCode")
    state = str(item.get("State", "")).lower()
    try:
        exit_code_int = int(exit_code)
    except (TypeError, ValueError):
        exit_code_int = 0
    status_by_service[service] = (exit_code_int, state)

try:
    parsed = json.loads(raw)
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                feed(item)
    elif isinstance(parsed, dict):
        feed(parsed)
except json.JSONDecodeError:
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            feed(item)

for phase in phase_order:
    if phase not in status_by_service:
        continue
    exit_code, state = status_by_service[phase]
    if exit_code != 0:
        print(phase)
        sys.exit(0)
    if state in {"dead"}:
        print(phase)
        sys.exit(0)

print("")
'
}

if [ "$DEBUG_MODE" = true ]; then
    # Debug mode: install debug packages, run dryrun, then record under GDB
    echo "📦 Installing debug packages..."
    
    # Install debug versions of packages
    docker run --rm \
        -v "$(pwd)/base-requirements-debug.txt:/app/dockertests/base-requirements.txt:ro" \
        -v "$(pwd)/$TEST_DIR:/app/test:ro" \
        -v "$(pwd)/.cache/pip:/root/.cache/pip" \
        -v "$(pwd)/$TEST_PACKAGES_DEBUG_DIR:/app/packages" \
        "$TEST_IMAGE" \
        bash -c "pip install --target /app/packages -r /app/dockertests/base-requirements.txt && \
                 if [ -f /app/test/requirements.txt ]; then pip install --target /app/packages -r /app/test/requirements.txt; fi"
    
    echo "🔍 Running dryrun..."
    docker run --rm \
        -v "$(pwd)/$TEST_DIR:/app/test:ro" \
        -v "$(pwd)/$TEST_PACKAGES_DEBUG_DIR:/app/packages:ro" \
        -e "PYTHONPATH=/app/packages" \
        "$TEST_IMAGE" \
        python /app/test/test.py
    
    echo ""
    echo "🐛 Starting record under GDB..."
    echo "   Commands: 'run' to start, 'bt' for backtrace on crash, 'quit' to exit"
    echo ""
    
    # Run record interactively with GDB and debug packages
    docker run -it --rm \
        --cap-add=SYS_PTRACE \
        -v "$(pwd)/$TEST_DIR:/app/test:ro" \
        -v "$(pwd)/$TEST_DIR/recording:/recording:rw" \
        -v "$(pwd)/$TEST_PACKAGES_DEBUG_DIR:/app/packages:ro" \
        -e "PYTHONPATH=/app/packages" \
        -e "RETRACE=1" \
        -e "RETRACE_RECORDING_PATH=/recording" \
        "$TEST_IMAGE" \
        bash -c "apt-get update -qq && apt-get install -qq -y gdb > /dev/null && \
                 python -m retracesoftware install && \
                 gdb -ex 'set confirm off' \
                     -ex 'handle SIGPIPE nostop noprint pass' \
                     -ex 'run' \
                     -ex 'bt full' \
                     --args python /app/test/test.py"
    DEBUG_EXIT_CODE=$?
    
    echo ""
    if [ "$DEBUG_EXIT_CODE" -eq 0 ]; then
        echo "✅ Debug session ended successfully."
    else
        echo "❌ Debug session failed (exit code: $DEBUG_EXIT_CODE)."
    fi
    echo "Recording may be incomplete."
    exit "$DEBUG_EXIT_CODE"
fi

# Normal mode: run full pipeline
set +e
$COMPOSE_CMD run --rm --quiet-pull cleanup
EXIT_CODE=$?
set -e


if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Test passed: $TEST_NAME"
else
    FAILED_PHASE="$(detect_failed_phase)"

    echo ""
    echo "❌ Test failed: $TEST_NAME (exit code: $EXIT_CODE)"
    if [ -n "$FAILED_PHASE" ]; then
        echo "❌ Failed phase: $FAILED_PHASE"
    else
        echo "❌ Failed phase: unknown (check logs below)"
    fi
    echo ""
    echo "📋 Logs from compose services:"
    echo "─────────────────────────────────────────────"
    if [ -n "$FAILED_PHASE" ]; then
        $COMPOSE_CMD logs --tail=120 "$FAILED_PHASE" || true
    fi
    $COMPOSE_CMD logs --tail=50 || true
    echo "─────────────────────────────────────────────"
fi

cleanup_compose

exit $EXIT_CODE
