#!/usr/bin/env python3
"""Analyze and summarize Retrace benchmark results."""

from __future__ import annotations

from pathlib import Path
import re
import sys


MetricMap = dict[str, dict[str, float]]


def parse_benchmark_file(filename: Path) -> MetricMap:
    if not filename.exists():
        print(f"Error: {filename} not found. Run benchmarks first.")
        sys.exit(1)

    content = filename.read_text(encoding="utf-8")
    results: MetricMap = {}

    average_pattern = re.compile(r"^(?P<name>[a-zA-Z0-9_]+) average_ms: (?P<avg>[0-9.]+)$")
    per_call_pattern = re.compile(
        r"^(?P<name>[a-zA-Z0-9_]+) per_(?:call|iteration)_ns: (?P<ns>[0-9.]+)$"
    )

    for line in content.splitlines():
        if match := average_pattern.match(line):
            name = match.group("name")
            results.setdefault(name, {})["avg_ms"] = float(match.group("avg"))
        elif match := per_call_pattern.match(line):
            name = match.group("name")
            results.setdefault(name, {})["per_call_ns"] = float(match.group("ns"))

    return results


def calculate_overhead(base_ms: float, retrace_ms: float) -> float:
    if base_ms == 0:
        return 0.0
    return ((retrace_ms - base_ms) / base_ms) * 100


def print_comparison(
    title: str,
    baseline: MetricMap,
    retrace: MetricMap,
    *,
    expect_internal: bool,
) -> None:
    print(title)
    print("-" * len(title))

    for name, base_data in baseline.items():
        ret_data = retrace.get(name)
        if not ret_data:
            print(f"{name}: missing retrace result")
            continue

        base_ms = base_data.get("avg_ms", 0.0)
        ret_ms = ret_data.get("avg_ms", 0.0)
        base_ns = base_data.get("per_call_ns", 0.0)
        ret_ns = ret_data.get("per_call_ns", 0.0)
        percentage = calculate_overhead(base_ms, ret_ms)
        absolute_ns = ret_ns - base_ns

        print(f"{name}:")
        print(f"  baseline: {base_ms:.3f}ms ({base_ns:.1f}ns/call)")
        print(f"  retrace:  {ret_ms:.3f}ms ({ret_ns:.1f}ns/call)")
        if expect_internal:
            print(f"  overhead: {percentage:+.2f}% (expected near measurement noise)")
        else:
            print(f"  overhead: {absolute_ns:+.1f}ns/call ({percentage:+.2f}%)")
            print("  note: percentage is denominator-sensitive; read the absolute ns")
        print()


def main() -> None:
    results_dir = Path("results")
    if not results_dir.exists():
        print("Error: results/ directory not found. Run ./run_all_benchmarks.sh first.")
        sys.exit(1)

    internal_base = parse_benchmark_file(results_dir / "internal_baseline.txt")
    internal_ret = parse_benchmark_file(results_dir / "internal_retrace.txt")
    external_base = parse_benchmark_file(results_dir / "external_baseline.txt")
    external_ret = parse_benchmark_file(results_dir / "external_retrace.txt")

    print("=" * 80)
    print("RETRACE BENCHMARK ANALYSIS")
    print("=" * 80)
    print()

    print_comparison(
        "Internal code benchmarks",
        internal_base,
        internal_ret,
        expect_internal=True,
    )
    print_comparison(
        "External boundary benchmarks",
        external_base,
        external_ret,
        expect_internal=False,
    )

    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print("Typical web-service overhead target: under 0.1% of request latency.")
    print("Boundary-call overhead target: approximately 200ns absolute cost.")
    print("Synthetic sub-microsecond operations can show high percentages because")
    print("the denominator is artificially small.")


if __name__ == "__main__":
    main()
