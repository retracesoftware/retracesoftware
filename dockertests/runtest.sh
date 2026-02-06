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
else
    BASE_COMPOSE="docker-compose.base.yml"
    TEST_TYPE="script"
fi

echo "🧪 Running test: $TEST_NAME ($TEST_TYPE)"
echo "   Image: $TEST_IMAGE"
if [ "$DEBUG_MODE" = true ]; then
    echo "   Mode: DEBUG (GDB)"
fi
echo ""

# Export variables
export TEST_IMAGE
export TEST_DIR

# Build compose command
COMPOSE_CMD="docker compose --progress plain -f $BASE_COMPOSE"
if [ -f "$TEST_DIR/docker-compose.yml" ]; then
    COMPOSE_CMD="$COMPOSE_CMD -f $TEST_DIR/docker-compose.yml"
fi

if [ "$DEBUG_MODE" = true ]; then
    # Debug mode: install debug packages, run dryrun, then record under GDB
    echo "📦 Installing debug packages..."
    
    # Install debug versions of packages
    docker run --rm \
        -v "$(pwd)/base-requirements-debug.txt:/app/dockertests/base-requirements.txt:ro" \
        -v "$(pwd)/$TEST_DIR:/app/test:ro" \
        -v "$(pwd)/.cache/pip:/root/.cache/pip" \
        -v "$(pwd)/.cache/packages-debug:/app/packages" \
        "$TEST_IMAGE" \
        bash -c "pip install --target /app/packages -r /app/dockertests/base-requirements.txt && \
                 [ -f /app/test/requirements.txt ] && pip install --target /app/packages -r /app/test/requirements.txt || true"
    
    echo "🔍 Running dryrun..."
    docker run --rm \
        -v "$(pwd)/$TEST_DIR:/app/test:ro" \
        -v "$(pwd)/.cache/packages-debug:/app/packages:ro" \
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
        -v "$(pwd)/../src:/app/src:ro" \
        -v "$(pwd)/$TEST_DIR:/app/test:ro" \
        -v "$(pwd)/$TEST_DIR/recording:/recording:rw" \
        -v "$(pwd)/.cache/packages-debug:/app/packages:ro" \
        -e "PYTHONPATH=/app/src:/app/packages" \
        "$TEST_IMAGE" \
        bash -c "apt-get update -qq && apt-get install -qq -y gdb > /dev/null && \
                 gdb -ex 'set confirm off' \
                     -ex 'handle SIGPIPE nostop noprint pass' \
                     -ex 'run' \
                     -ex 'bt full' \
                     --args python -m retracesoftware --recording /recording -- /app/test/test.py"
    
    echo ""
    echo "Debug session ended. Recording may be incomplete."
    exit 0
fi

# Normal mode: run full pipeline
set +e
$COMPOSE_CMD run --rm --quiet-pull cleanup
EXIT_CODE=$?
set -e


if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Test passed: $TEST_NAME"
else
    echo ""
    echo "❌ Test failed: $TEST_NAME (exit code: $EXIT_CODE)"
    echo ""
    echo "📋 Logs from failed services:"
    echo "─────────────────────────────────────────────"
    $COMPOSE_CMD logs --tail=50
    echo "─────────────────────────────────────────────"
fi

exit $EXIT_CODE
