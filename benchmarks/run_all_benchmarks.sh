#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESULTS_DIR="$ROOT_DIR/results"
mkdir -p "$RESULTS_DIR"

cd "$ROOT_DIR"

rm -f "$RESULTS_DIR"/*.retrace

{
  echo "Retrace benchmark run"
  date -u +"timestamp_utc: %Y-%m-%dT%H:%M:%SZ"
  python -V
  echo

  echo "== synthetic baseline =="
  python synthetic_benchmark.py | tee "$RESULTS_DIR/internal_baseline.txt"
  echo

  echo "== synthetic with retrace =="
  RETRACE_RECORDING="$RESULTS_DIR/internal.retrace" \
    python synthetic_benchmark.py | tee "$RESULTS_DIR/internal_retrace.txt"
  echo

  echo "== external boundary baseline =="
  python external_boundary_benchmark.py | tee "$RESULTS_DIR/external_baseline.txt"
  echo

  echo "== external boundary with retrace =="
  RETRACE_RECORDING="$RESULTS_DIR/external_boundary.retrace" \
    python external_boundary_benchmark.py | tee "$RESULTS_DIR/external_retrace.txt"
  echo

  echo "== analysis =="
  python analyze_results.py
} | tee "$RESULTS_DIR/summary.txt"
