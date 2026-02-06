import json
import os
import statistics
import time

import requests


SERVER_URL = os.environ.get("SERVER_URL", "http://localhost:5000")
PING_PATH = os.environ.get("PING_PATH", "/ping")
REQUESTS_COUNT = int(os.environ.get("REQUESTS_COUNT", "5000"))
WARMUP_COUNT = int(os.environ.get("WARMUP_COUNT", "10"))
RUNS = int(os.environ.get("RUNS", "1"))
CLIENT_MODE = os.environ.get("CLIENT_MODE", "unknown")
RESULT_PATH = os.environ.get("RESULT_PATH")


def percentile(sorted_values, pct):
    if not sorted_values:
        return 0.0
    k = int(round((pct / 100.0) * (len(sorted_values) - 1)))
    return sorted_values[k]


def main() -> None:
    url = f"{SERVER_URL}{PING_PATH}"
    session = requests.Session()

    latencies_ms = []
    start_total = time.perf_counter()
    for _run in range(RUNS):
        for _ in range(WARMUP_COUNT):
            resp = session.get(url)
            resp.raise_for_status()

        for _ in range(REQUESTS_COUNT):
            start = time.perf_counter()
            resp = session.get(url)
            resp.raise_for_status()
            latencies_ms.append((time.perf_counter() - start) * 1000.0)
    total_time = time.perf_counter() - start_total

    latencies_ms.sort()
    avg_ms = statistics.mean(latencies_ms)
    median_ms = statistics.median(latencies_ms)
    p95_ms = percentile(latencies_ms, 95)
    p99_ms = percentile(latencies_ms, 99)
    min_ms = latencies_ms[0]
    max_ms = latencies_ms[-1]

    summary = {
        "mode": CLIENT_MODE,
        "requests": REQUESTS_COUNT,
        "runs": RUNS,
        "total_requests": REQUESTS_COUNT * RUNS,
        "total_time_s": round(total_time, 4),
        "avg_ms": round(avg_ms, 3),
        "median_ms": round(median_ms, 3),
        "p95_ms": round(p95_ms, 3),
        "p99_ms": round(p99_ms, 3),
        "min_ms": round(min_ms, 3),
        "max_ms": round(max_ms, 3),
    }

    print("=== HTTP perf results ===", flush=True)
    for key, value in summary.items():
        print(f"{key}: {value}", flush=True)

    if RESULT_PATH:
        with open(RESULT_PATH, "w", encoding="utf-8") as handle:
            json.dump(summary, handle, indent=2)


if __name__ == "__main__":
    main()
