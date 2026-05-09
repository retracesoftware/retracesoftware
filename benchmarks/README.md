# Retrace Performance Benchmarks

## Headline

Retrace's recording overhead is under 0.1% of request latency on typical web
service workloads. This is because Retrace records at the Python-to-external
boundary rather than at every instruction: each intercepted boundary call adds
approximately 200 nanoseconds of overhead, and internal Python code
(computation, control flow, object manipulation) is not intercepted at all.

For a typical 50ms web request making two external calls, the absolute
recording overhead is approximately 400 nanoseconds, or 0.0008% of request
latency. The under-0.1% headline is the conservative reading across realistic
web service workloads.

Workloads with very high-frequency boundary crossings, such as CPU-bound code
making large numbers of small system calls, will see higher percentage overhead
because the same 200 nanoseconds is divided by a smaller denominator. The
synthetic benchmarks in this directory measure the absolute cost in isolation;
the worked example below shows how that translates to percentage overhead on
realistic workloads.

## Worked Example: A 50ms Web Request

```text
Total request time: 50ms
|-- 40ms: Internal business logic (0% overhead, not intercepted)
|--  8ms: Database query
|        `-- +200ns Retrace overhead
`--  2ms: Cache lookup
         `-- +200ns Retrace overhead
```

Total Retrace overhead: 400ns over 50ms = 0.0008%.

The 400 nanoseconds is the cost of intercepting two boundary calls, the
database query and the cache lookup, and serializing the recorded results.
Internal business logic, where most of the request time is actually spent, is
not intercepted at all.

The same 200ns absolute cost divided by any millisecond-scale external call
sits well below 0.1%. The 0.0008% in the example is the typical case for
I/O-bound web services.

## Absolute Cost Vs Percentage Overhead

The 200ns absolute overhead is roughly constant regardless of what the
underlying call does. The percentage overhead depends entirely on the latency
of the operation being wrapped.

| Operation | Typical latency | Retrace overhead | Overhead |
| --- | ---: | ---: | ---: |
| `os.environ.get()` (synthetic) | ~0.4us | +200ns | ~50% |
| Localhost socket read | ~30us | +200ns | ~0.7% |
| Database query | ~1ms | +200ns | ~0.02% |
| HTTP API call | ~10ms | +200ns | ~0.002% |

In production, external calls are usually at least microsecond-scale, so the
percentage overhead is negligible. In synthetic tight-loop benchmarks where the
wrapped operation itself is sub-microsecond, the same 200ns can appear as 30 to
50 percent overhead. This is expected and is not a concern in real
applications.

Read the absolute number, not the percentage. The percentage on
sub-microsecond operations is misleading because the denominator is
artificially small. The absolute 200ns is the real story.

## Two Categories Of Operations

Internal code is not intercepted and runs at native speed:

- Ordinary deterministic Python functions
- Pure computation
- Object manipulation
- Control flow

External boundaries are intercepted and add approximately 200ns overhead per
call:

- Network calls, including HTTP and database calls
- File I/O
- Environment variables
- Time, randomness, and system state queries

This split is the architectural choice that determines Retrace's overhead
profile. Recording at the Python-to-external boundary, rather than across all
code, is what keeps overhead negligible on workloads where most time is spent
in internal logic with millisecond-scale external calls.

## Quick Start

Install Retrace and enable the startup hook in your active environment:

```bash
pip install retracesoftware
python -m retracesoftware install
```

Run all benchmarks:

```bash
./run_all_benchmarks.sh
```

View results:

```bash
cat results/summary.txt
```

For a shorter command reference, see [quickstart.md](quickstart.md).

## Available Benchmarks

### 1. Internal Code Benchmark (`synthetic_benchmark.py`)

Measures operations that Retrace does not intercept. Expected result is 0%
overhead.

Tests:

- Pure arithmetic and branching in a tight loop
- Object allocation and dictionary/list manipulation

Baseline:

```bash
python synthetic_benchmark.py
```

With Retrace:

```bash
RETRACE_RECORDING=/tmp/recording python synthetic_benchmark.py
```

### 2. External Boundary Benchmark (`external_boundary_benchmark.py`)

Measures operations that Retrace does intercept. Expected result is
approximately 200ns absolute overhead per call.

Tests:

- Environment variable access (`os.environ.get()`)
- File read operations (`open()` / `read()`)
- Time calls (`time.time()` and `datetime.now()`)

Baseline:

```bash
python external_boundary_benchmark.py
```

With Retrace:

```bash
RETRACE_RECORDING=/tmp/recording python external_boundary_benchmark.py
```

## Benchmark Environment

- Benchmarks can be run under supported CPython versions.
- Reference runs use Python 3.11.
- Single-threaded execution
- 5 runs per test, averaged
- Warmup runs, discarded
- Test machines: macOS (Apple Silicon, M2, 16GB), Linux (Ubuntu 22.04, x86_64,
  32GB)

System information is captured in each benchmark run output: Python version,
OS and architecture, CPU count, timestamp.

## Interpreting Results

### Metrics

- Throughput (ops/sec): higher is better.
- Per-operation time (us or ms): lower is better, and more intuitive for
  understanding overhead.
- Overhead calculation: `(With_Retrace - Baseline) / Baseline * 100%`

### Expected Ranges

Internal code benchmark:

- 0 to 5% overhead, within measurement noise
- Per-call: less than 1us difference

External boundary benchmark:

- Per-call: approximately 200ns absolute overhead
- Sub-microsecond operations show 30 to 50% percentage overhead in isolation,
  which is the expected artefact of a small denominator
- Real I/O operations, such as database and network calls: well under 1%

## Production Benchmarking

Synthetic benchmarks show theoretical overhead. To measure real overhead in
your application, compare the same workload with and without Retrace.

Baseline measurement:

```bash
ab -n 1000 -c 10 http://localhost:8000/api/endpoint
```

With Retrace recording:

```bash
RETRACE_RECORDING=/tmp/recording python app.py
ab -n 1000 -c 10 http://localhost:8000/api/endpoint
```

Calculate overhead:

```text
Overhead = (Baseline_RPS - Retrace_RPS) / Baseline_RPS * 100%
```

Existing APM tools, such as Datadog or New Relic, will also surface this as
request latency or throughput changes on the relevant endpoints.

## Custom Benchmark Template

```python
"""Custom benchmark for [your use case]."""

import time


def baseline_operation():
    """Your operation without Retrace."""
    pass


def measure_operation(iterations=10000):
    start = time.perf_counter()
    for _ in range(iterations):
        baseline_operation()
    return time.perf_counter() - start


def run_benchmark():
    measure_operation(100)  # warmup

    runs = []
    for i in range(5):
        elapsed = measure_operation(10000)
        runs.append(elapsed)
        print(f"  Run {i + 1}: {elapsed * 1000:.2f}ms")

    avg = sum(runs) / len(runs)
    print(f"  Average:       {avg * 1000:.2f}ms")
    print(f"  Per-operation: {(avg / 10000) * 1000000:.2f}us")


if __name__ == "__main__":
    run_benchmark()
```

Run the baseline, then run with Retrace and compare:

```bash
RETRACE_RECORDING=/tmp/recording python your_benchmark.py
```

## Troubleshooting

### High Variance Between Runs

Symptoms: results vary by more than 20% between runs.

Causes: background processes, thermal throttling, swap activity, network
interference.

Solutions: close other applications, disable background services, run multiple
times, use a dedicated benchmark machine.

### Higher Than Expected Overhead

Symptoms: more than 1us per operation, or more than 1% on real endpoints.

Causes: large payloads being serialized, complex deeply nested object graphs,
slow disk I/O for trace writes.

Solutions: record a subset of requests, adjust trace buffer size, use faster
storage for trace files, profile with the Python profiler to identify the
bottleneck.

## FAQ

### Why Do Internal Code Tests Show 0% Overhead?

Internal deterministic Python code is not intercepted. Only external boundary
crossings, such as network, database, file I/O, time, randomness, and system
state, are recorded.

### Why Does The Percentage Overhead On Synthetic Tight Loops Look High?

In tight loops, we are measuring proxy cost without real I/O latency. The 200ns
absolute overhead is around 50% of a 0.4us `os.environ.get()` call, but only
0.02% of a 1ms database query. The absolute 200ns is constant; only the
denominator changes. In production, external calls are usually
microsecond-scale or slower, so percentage overhead is negligible.

### Should I Run These Benchmarks Before Deploying Retrace?

Optional but recommended. Synthetic benchmarks give you the theoretical
overhead. Production benchmarking on your actual workload, using `ab` or your
existing APM, gives you the real number.

### Will Overhead Change With Future Retrace Versions?

The 200ns figure is the current measurement; we track it across versions and
will publish updated numbers as the recording path is optimised further.
