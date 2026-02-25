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

# Timeout controls (seconds). Override per run via env vars.
PIPELINE_TIMEOUT_SEC="${RETRACE_PIPELINE_TIMEOUT_SEC:-600}"

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

# Ensure per-test recording directory exists and stale traces are removed
# before each run, so failed prior runs cannot poison current results.
mkdir -p "$TEST_DIR/recording"
if [ ! -d "$TEST_DIR/recording" ]; then
    echo "❌ Failed to prepare recording directory: $TEST_DIR/recording"
    exit 1
fi
rm -f "$TEST_DIR/recording/trace.bin" "$TEST_DIR/recording/trace.bin.lock"

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
SAFE_IMAGE_TAG="$(printf '%s' "$TEST_IMAGE" | tr '/:@' '___' | tr -c '[:alnum:]_.-' '_' | tr -d '\n')"
SAFE_TEST_NAME="$(printf '%s' "$TEST_NAME" | tr -c '[:alnum:]_.-' '_' | tr -d '\n')"
mkdir -p "./.cache/packages" "./.cache/packages-debug" "./.cache/pip"
TEST_PACKAGES_DIR="$(pwd)/.cache/packages/${SAFE_TEST_NAME}_${SAFE_IMAGE_TAG}"
TEST_PACKAGES_DEBUG_DIR="$(pwd)/.cache/packages-debug/${SAFE_TEST_NAME}_${SAFE_IMAGE_TAG}"
COMPOSE_PROJECT_NAME="retracetest_${SAFE_TEST_NAME}_$(date +%s)_$$"
mkdir -p "$TEST_PACKAGES_DIR" "$TEST_PACKAGES_DEBUG_DIR"
if [ ! -d "$TEST_PACKAGES_DIR" ] || [ ! -d "$TEST_PACKAGES_DEBUG_DIR" ]; then
    echo "❌ Failed to create package cache directories"
    echo "   TEST_PACKAGES_DIR=$TEST_PACKAGES_DIR"
    echo "   TEST_PACKAGES_DEBUG_DIR=$TEST_PACKAGES_DEBUG_DIR"
    exit 1
fi

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

# Always cleanup compose resources even on unexpected early exits.
trap cleanup_compose EXIT

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

detect_active_phase() {
    local compose_output
    compose_output="$($COMPOSE_CMD ps -a --format json 2>/dev/null || true)"
    if [ -z "$compose_output" ]; then
        echo ""
        return
    fi

    local phases_csv
    phases_csv="$(IFS=,; echo "${PHASE_SERVICES[*]}")"

    # Find the earliest phase still running to classify timeout hangs.
    printf '%s\n' "$compose_output" | PHASE_SERVICES_CSV="$phases_csv" python -c '
import json
import os
import sys

phase_order = [p for p in os.environ.get("PHASE_SERVICES_CSV", "").split(",") if p]
state_by_service = {}

raw = sys.stdin.read().strip()
if not raw:
    print("")
    sys.exit(0)

def feed(item):
    service = item.get("Service")
    if not service:
        return
    state = str(item.get("State", "")).lower()
    state_by_service[service] = state

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
    if state_by_service.get(phase) in {"running", "restarting"}:
        print(phase)
        sys.exit(0)

print("")
'
}

run_with_timeout() {
    local timeout_sec="$1"
    shift
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$timeout_sec" "$@" <<'PY'
import os
import signal
import subprocess
import sys

timeout_sec = int(sys.argv[1])
cmd = sys.argv[2:]
if not cmd:
    sys.exit(2)

proc = subprocess.Popen(cmd, preexec_fn=os.setsid)
try:
    proc.wait(timeout=timeout_sec)
    sys.exit(proc.returncode)
except subprocess.TimeoutExpired:
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()
    sys.exit(124)
PY
    else
        "$@"
    fi
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
        bash -c "shopt -s dotglob nullglob && rm -rf /app/packages/* && shopt -u dotglob nullglob && \
                 pip install --no-cache-dir --upgrade --target /app/packages -r /app/dockertests/base-requirements.txt && \
                 if [ -f /app/test/requirements.txt ]; then pip install --no-cache-dir --upgrade --target /app/packages -r /app/test/requirements.txt; fi"
    
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
        -w /recording \
        -e "PYTHONPATH=/app/packages" \
        -e "RETRACE_CONFIG=debug" \
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
    if [ "$DEBUG_EXIT_CODE" -eq 0 ] && [ ! -f "$TEST_DIR/recording/trace.bin" ]; then
        echo "❌ Debug record completed but trace.bin is missing at $TEST_DIR/recording/trace.bin"
        exit 1
    fi
    echo "Recording may be incomplete."
    exit "$DEBUG_EXIT_CODE"
fi

# Normal mode: run full pipeline
set +e
run_with_timeout "$PIPELINE_TIMEOUT_SEC" bash -lc "$COMPOSE_CMD run --rm --quiet-pull cleanup"
EXIT_CODE=$?
set -e

TIMED_OUT_PHASE=""
if [ "$EXIT_CODE" -eq 124 ]; then
    TIMED_OUT_PHASE="$(detect_active_phase)"
fi

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Test passed: $TEST_NAME"
else
    FAILED_PHASE="$(detect_failed_phase)"
    if [ "$EXIT_CODE" -eq 124 ] && [ -n "$TIMED_OUT_PHASE" ]; then
        FAILED_PHASE="$TIMED_OUT_PHASE"
    fi

    echo ""
    echo "❌ Test failed: $TEST_NAME (exit code: $EXIT_CODE)"
    if [ "$EXIT_CODE" -eq 124 ]; then
        echo "⏱️  Pipeline timed out after ${PIPELINE_TIMEOUT_SEC}s"
        if [ -n "$TIMED_OUT_PHASE" ]; then
            echo "⏱️  Timed out phase: $TIMED_OUT_PHASE"
        fi
    fi
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

exit $EXIT_CODE
