import json
from pathlib import Path


HERE = Path(__file__).parent
DRYRUN_PATH = HERE / "results-dryrun.json"
RECORD_PATH = HERE / "results-record.json"


def load(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Missing results file: {path}")
    return json.loads(path.read_text())


def pct_change(base: float, new: float) -> float:
    if base == 0:
        return 0.0
    return ((new - base) / base) * 100.0


def main() -> None:
    dry = load(DRYRUN_PATH)
    rec = load(RECORD_PATH)

    total_dry = float(dry.get("total_time_s", 0.0))
    total_rec = float(rec.get("total_time_s", 0.0))
    avg_dry = float(dry.get("avg_ms", 0.0))
    avg_rec = float(rec.get("avg_ms", 0.0))
    runs = int(dry.get("runs", 1))
    total_requests = int(dry.get("total_requests", 0))

    total_pct = pct_change(total_dry, total_rec)
    avg_pct = pct_change(avg_dry, avg_rec)
    total_delta = total_rec - total_dry
    avg_delta = avg_rec - avg_dry

    lines = [
        "=== HTTP perf summary ===",
        (
            "total_time_s: without_retrace={:.4f}, with_retrace={:.4f}, "
            "delta={:.4f}s, overhead={:+.1f}%"
        ).format(total_dry, total_rec, total_delta, total_pct),
        (
            "avg_ms_per_request: without_retrace={:.3f}, with_retrace={:.3f}, "
            "delta={:.3f}ms, overhead={:+.1f}%"
        ).format(avg_dry, avg_rec, avg_delta, avg_pct),
        f"runs: {runs}, total_requests: {total_requests}",
    ]

    if avg_pct >= 0:
        lines.append(f"With retrace, average response time is {avg_pct:.1f}% longer.")
    else:
        lines.append(f"With retrace, average response time is {abs(avg_pct):.1f}% shorter.")

    for line in lines:
        print(line)

    summary_path = HERE / "summary.txt"
    summary_path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
