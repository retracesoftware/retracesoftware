# Retrace Benchmarks - Quick Reference

## TL;DR

**Typical web services:** under 0.1% request-latency overhead  
**External boundaries:** approximately 200ns absolute overhead per intercepted call  
**Internal code:** native speed, not intercepted

The percentage shown by synthetic tight-loop benchmarks can look high for
sub-microsecond operations. Read the absolute number first: 200ns is the useful
measurement.

## Running Benchmarks

```bash
pip install retracesoftware
python -m retracesoftware install

# Quick run (all benchmarks)
./run_all_benchmarks.sh

# Individual tests
python synthetic_benchmark.py
RETRACE_RECORDING=/tmp/synthetic.retrace python synthetic_benchmark.py

python external_boundary_benchmark.py
RETRACE_RECORDING=/tmp/external.retrace python external_boundary_benchmark.py

# Detailed analysis
python analyze_results.py
```

## Understanding Results

### Benchmark A: Internal Code

What it tests:

- Pure arithmetic and branching
- Object allocation and dictionary/list manipulation
- Deterministic Python control flow

Expected result: 0 to 5% overhead, within measurement noise.

Why: deterministic internal code is not intercepted by Retrace.

### Benchmark B: External Boundaries

What it tests:

- Environment variable access
- File I/O operations
- Time and datetime calls

Expected result: approximately 200ns absolute overhead per intercepted call.

Why: Retrace captures arguments, results, and writes the boundary result to the
trace.

## Real-World Translation

### Example: Web API Request

```text
Total: 50ms
|-- 40ms: Business logic (0% overhead)
|--  8ms: Database query
|   `-- +200ns Retrace
`--  2ms: Cache lookup
    `-- +200ns Retrace

Total overhead: 400ns / 50ms = 0.0008%
```

### Key Insight

Retrace overhead is invisible compared to real I/O latency. A database query or
HTTP call usually takes milliseconds; the recording boundary adds about 200ns.

## Files Generated

```text
benchmarks/
|-- results/
|   |-- internal_baseline.txt
|   |-- internal_retrace.txt
|   |-- external_baseline.txt
|   |-- external_retrace.txt
|   `-- summary.txt
|-- synthetic_benchmark.py
|-- external_boundary_benchmark.py
|-- run_all_benchmarks.sh
`-- analyze_results.py
```

## Production Benchmarking

Baseline:

```bash
ab -n 1000 -c 10 http://localhost:8000/api/endpoint
```

With Retrace:

```bash
RETRACE_RECORDING=/tmp/recording python app.py
ab -n 1000 -c 10 http://localhost:8000/api/endpoint
```

Compare throughput or latency in your load-test tool or APM.

## Troubleshooting

### High Variance

Symptoms: results vary by more than 20% between runs.

Fixes:

- Close other apps
- Disable background services
- Run multiple times
- Check CPU throttling

### Unexpected High Overhead

Symptoms: more than 1us per operation, or more than 1% on real endpoints.

Check:

- Large payloads being serialized
- Disk I/O bottleneck
- Correct Python environment and Retrace install

Fix:

- Record a subset of requests
- Use faster storage for traces
- Profile with `cProfile`

## Questions?

**Why do internal-code tests show 0% overhead?**  
Because deterministic internal Python code is not intercepted.

**Why can `os.environ.get()` show a high percentage?**  
The operation itself is tiny. 200ns is large compared with 0.4us, but tiny
compared with a 1ms database query.

**Should I run these before deploying?**  
Optional. They show theoretical overhead. Production benchmarking shows actual
overhead on your workload.

See [README.md](README.md) for the full benchmark guide.
