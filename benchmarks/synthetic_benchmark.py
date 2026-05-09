"""Synthetic benchmark for deterministic internal Python code.

These operations should mostly run at native speed under Retrace because they
do not cross the Python-to-external boundary.
"""

from __future__ import annotations

import platform
import statistics
import time


ITERATIONS = 200_000
RUNS = 5
WARMUP = 2


def arithmetic_and_branching(iterations: int = ITERATIONS) -> int:
    total = 0
    for i in range(iterations):
        value = (i * 17) ^ (i >> 3)
        if value & 1:
            total += value % 97
        else:
            total -= value % 89
    return total


def object_manipulation(iterations: int = ITERATIONS) -> int:
    items: list[dict[str, int]] = []
    for i in range(iterations // 20):
        items.append({"index": i, "value": i * 3})
    return sum(item["value"] for item in items)


def measure(name: str, func) -> None:
    for _ in range(WARMUP):
        func()

    runs = []
    for index in range(RUNS):
        start = time.perf_counter()
        result = func()
        elapsed = time.perf_counter() - start
        runs.append(elapsed)
        print(f"{name} run {index + 1}: {elapsed * 1000:.3f}ms result={result}")

    avg = statistics.mean(runs)
    print(f"{name} average_ms: {avg * 1000:.3f}")
    print(f"{name} per_iteration_ns: {(avg / ITERATIONS) * 1_000_000_000:.1f}")


def main() -> None:
    print("benchmark: synthetic internal Python")
    print(f"python: {platform.python_version()}")
    print(f"platform: {platform.platform()}")
    print(f"iterations: {ITERATIONS}")
    measure("arithmetic_and_branching", arithmetic_and_branching)
    measure("object_manipulation", object_manipulation)


if __name__ == "__main__":
    main()
