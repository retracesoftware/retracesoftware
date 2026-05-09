"""Synthetic benchmark for Retrace external-boundary calls."""

from __future__ import annotations

import datetime as _datetime
import os
import platform
import statistics
import tempfile
import time
from pathlib import Path


ITERATIONS = 50_000
RUNS = 5
WARMUP = 2


def env_get(iterations: int = ITERATIONS) -> int:
    total = 0
    for _ in range(iterations):
        total += len(os.environ.get("PATH", ""))
    return total


def file_read(iterations: int = ITERATIONS) -> int:
    total = 0
    with tempfile.TemporaryDirectory() as tempdir:
        path = Path(tempdir) / "payload.txt"
        path.write_text("retracesoftware benchmark payload\n", encoding="utf-8")
        for _ in range(iterations):
            total += len(path.read_text(encoding="utf-8"))
    return total


def time_calls(iterations: int = ITERATIONS) -> int:
    total = 0
    for _ in range(iterations):
        total += int(time.time()) & 1
        total += _datetime.datetime.now().microsecond & 1
    return total


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
    print(f"{name} per_call_ns: {(avg / ITERATIONS) * 1_000_000_000:.1f}")


def main() -> None:
    print("benchmark: external boundaries")
    print(f"python: {platform.python_version()}")
    print(f"platform: {platform.platform()}")
    print(f"iterations: {ITERATIONS}")
    measure("os_environ_get", env_get)
    measure("file_read", file_read)
    measure("time_and_datetime", time_calls)


if __name__ == "__main__":
    main()
