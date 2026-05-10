#!/usr/bin/env python3
"""Discover and run Retrace docker scenario tests."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_IMAGE = "python:3.12"


@dataclass
class TestInfo:
    path: Path
    name: str
    tags: list[str] = field(default_factory=list)
    has_compose: bool = False
    has_requirements: bool = False
    is_server: bool = False


@dataclass
class TestResult:
    name: str
    success: bool
    duration: float
    error: str = ""


def load_tags(test_dir: Path) -> list[str]:
    tags_file = test_dir / "tags"
    if not tags_file.exists():
        return []

    try:
        with tags_file.open() as f:
            return [
                line.strip()
                for line in f
                if line.strip() and not line.lstrip().startswith("#")
            ]
    except OSError:
        return []


def discover_tests() -> list[TestInfo]:
    tests_dir = Path(__file__).parent / "tests"
    if not tests_dir.exists():
        return []

    tests: list[TestInfo] = []
    for entry in sorted(tests_dir.iterdir()):
        if entry.is_dir() and (entry / "test.py").exists():
            tests.append(
                TestInfo(
                    path=entry,
                    name=entry.name,
                    tags=load_tags(entry),
                    has_compose=(entry / "docker-compose.yml").exists(),
                    has_requirements=(entry / "requirements.txt").exists(),
                    is_server=(entry / "client.py").exists(),
                )
            )
    return tests


def parse_excludes(exclude_args: list[str]) -> set[str]:
    excluded: set[str] = set()
    for item in exclude_args:
        for part in item.split(","):
            part = part.strip()
            if part:
                excluded.add(part)
    return excluded


def filter_by_tags(tests: list[TestInfo], tags: list[str]) -> list[TestInfo]:
    tag_set = set(tags)
    return [test for test in tests if tag_set & set(test.tags)]


def run_test(
    test_name: str,
    *,
    image: str,
    record_mode: str,
    replay_mode: str,
    retrace_config: str,
    keep_recording: bool,
    timeout: int,
) -> TestResult:
    script = Path(__file__).parent / "runtest.sh"
    cmd = [
        str(script),
        test_name,
        "--image",
        image,
        "--record-mode",
        record_mode,
        "--replay-mode",
        replay_mode,
        "--retrace-config",
        retrace_config,
        "--timeout",
        str(timeout),
    ]
    if keep_recording:
        cmd.append("--keep-recording")

    start = time.time()
    try:
        result = subprocess.run(cmd, cwd=script.parent, text=True)
    except Exception as exc:
        return TestResult(
            name=test_name,
            success=False,
            duration=time.time() - start,
            error=str(exc),
        )

    duration = time.time() - start
    if result.returncode == 0:
        return TestResult(name=test_name, success=True, duration=duration)

    return TestResult(
        name=test_name,
        success=False,
        duration=duration,
        error=f"runtest.sh exited with code {result.returncode}",
    )


def clean_harness_state(dockertests_dir: Path) -> None:
    print("Cleaning harness state...")

    cache_dirs = [
        dockertests_dir / ".cache" / "packages",
        dockertests_dir / ".cache" / "packages-debug",
        dockertests_dir / ".cache" / "pip",
    ]
    for path in cache_dirs:
        if path.exists():
            shutil.rmtree(path)
            print(f"   removed {path}")

    tests_dir = dockertests_dir / "tests"
    removed = 0
    if tests_dir.exists():
        for recording_dir in tests_dir.glob("*/recording"):
            if not recording_dir.is_dir():
                continue
            for stale in recording_dir.iterdir():
                if stale.is_dir():
                    shutil.rmtree(stale)
                else:
                    stale.unlink()
                removed += 1
    if removed:
        print(f"   removed stale recording entries: {removed}")

    try:
        _clean_stale_compose_objects()
    except Exception as exc:
        print(f"   docker cleanup skipped: {exc}")


def _clean_stale_compose_objects() -> None:
    containers = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()

    stale_containers: list[str] = []
    if containers:
        inspected = subprocess.run(
            ["docker", "inspect", *containers],
            capture_output=True,
            text=True,
            check=False,
        )
        if inspected.returncode == 0 and inspected.stdout.strip():
            for item in json.loads(inspected.stdout):
                labels = (item.get("Config") or {}).get("Labels") or {}
                project = labels.get("com.docker.compose.project", "")
                cid = item.get("Id")
                if project.startswith("retracetest_") and cid:
                    stale_containers.append(cid)

    if stale_containers:
        subprocess.run(
            ["docker", "rm", "-f", *stale_containers],
            capture_output=True,
            text=True,
            check=False,
        )
        print(f"   removed stale containers: {len(stale_containers)}")

    networks = subprocess.run(
        ["docker", "network", "ls", "-q", "--filter", "label=com.docker.compose.project"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.split()

    stale_networks: list[str] = []
    if networks:
        inspected = subprocess.run(
            ["docker", "network", "inspect", *networks],
            capture_output=True,
            text=True,
            check=False,
        )
        if inspected.returncode == 0 and inspected.stdout.strip():
            for item in json.loads(inspected.stdout):
                labels = item.get("Labels") or {}
                project = labels.get("com.docker.compose.project", "")
                nid = item.get("Id")
                if project.startswith("retracetest_") and nid:
                    stale_networks.append(nid)

    if stale_networks:
        subprocess.run(
            ["docker", "network", "rm", *stale_networks],
            capture_output=True,
            text=True,
            check=False,
        )
        print(f"   removed stale networks: {len(stale_networks)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Retrace docker scenario tests")
    parser.add_argument("tests", nargs="*", help="Specific tests to run")
    parser.add_argument("--list", "-l", action="store_true", help="List available tests")
    parser.add_argument("--tags", "-t", help="Run tests with these comma-separated tags")
    parser.add_argument(
        "--image",
        "-i",
        default=DEFAULT_IMAGE,
        help=f"Docker Python image to use (default: {DEFAULT_IMAGE})",
    )
    parser.add_argument(
        "--record-mode",
        choices=("pth", "direct"),
        default="pth",
        help="Recording entrypoint: .pth auto-enable flow or direct CLI wrapper",
    )
    parser.add_argument(
        "--replay-mode",
        choices=("pidfile", "recording"),
        default="pidfile",
        help="Replay extracted root PidFile or legacy unframed recording directly",
    )
    parser.add_argument(
        "--retrace-config",
        choices=("normal", "debug"),
        default="normal",
        help="Retrace config preset for record phase (default: normal)",
    )
    parser.add_argument(
        "--keep-recording",
        action="store_true",
        help="Keep recording artifacts after successful replay",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Pipeline timeout in seconds for one test",
    )
    parser.add_argument(
        "--exclude",
        "-x",
        action="append",
        default=[],
        help="Exclude tests by name; repeatable or comma-separated",
    )
    parser.add_argument(
        "--include-perf",
        action="store_true",
        help="Include perf-tagged tests, which are excluded by default",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Clean harness caches and stale compose artifacts before running",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    dockertests_dir = Path(__file__).parent
    if args.clean:
        clean_harness_state(dockertests_dir)

    all_tests = discover_tests()
    if not all_tests:
        print("No tests found in dockertests/tests/")
        sys.exit(1)

    if args.list:
        print(f"Found {len(all_tests)} test(s):")
        for test in all_tests:
            extras = []
            if test.is_server:
                extras.append("server")
            if test.tags:
                extras.append(f"tags: {', '.join(test.tags)}")
            if test.has_compose:
                extras.append("docker-compose.yml")
            if test.has_requirements:
                extras.append("requirements.txt")
            suffix = f" ({'; '.join(extras)})" if extras else ""
            print(f"  - {test.name}{suffix}")
        return

    if args.tests:
        requested = set(args.tests)
        tests_to_run = [test for test in all_tests if test.name in requested]
        for name in sorted(requested - {test.name for test in tests_to_run}):
            print(f"Warning: test not found: {name}")
    else:
        tests_to_run = all_tests

    if args.tags:
        tags = [tag.strip() for tag in args.tags.split(",") if tag.strip()]
        tests_to_run = filter_by_tags(tests_to_run, tags)
        if not tests_to_run:
            print(f"No tests found with tags: {', '.join(tags)}")
            return

    requested_tags = {tag.strip() for tag in (args.tags or "").split(",") if tag.strip()}
    requested_names = set(args.tests) if args.tests else set()
    if not args.include_perf and "perf" not in requested_tags:
        excluded_perf = [
            test.name
            for test in tests_to_run
            if "perf" in test.tags and test.name not in requested_names
        ]
        if excluded_perf:
            print(
                "Excluding perf tests; use --include-perf or --tags perf to run: "
                + ", ".join(excluded_perf)
            )
        tests_to_run = [
            test
            for test in tests_to_run
            if "perf" not in test.tags or test.name in requested_names
        ]

    excluded = parse_excludes(args.exclude)
    if excluded:
        available = {test.name for test in all_tests}
        for name in sorted(excluded - available):
            print(f"Warning: excluded test not found: {name}")
        tests_to_run = [test for test in tests_to_run if test.name not in excluded]

    if not tests_to_run:
        print("No tests to run")
        sys.exit(1)

    print(f"Running {len(tests_to_run)} test(s)")
    print(f"   Image: {args.image}")
    print(f"   Record mode: {args.record_mode}")
    print(f"   Replay mode: {args.replay_mode}")
    print(f"   Retrace config: {args.retrace_config}")
    print(f"   Keep recordings: {'yes' if args.keep_recording else 'no'}")
    if excluded:
        print(f"   Excluding: {', '.join(sorted(excluded))}")
    print("=" * 72)

    results: list[TestResult] = []
    for test in tests_to_run:
        print(f"\n>>> {test.name}")
        result = run_test(
            test.name,
            image=args.image,
            record_mode=args.record_mode,
            replay_mode=args.replay_mode,
            retrace_config=args.retrace_config,
            keep_recording=args.keep_recording,
            timeout=args.timeout,
        )
        results.append(result)

        status = "PASSED" if result.success else "FAILED"
        print(f"   {status} ({result.duration:.1f}s)")
        if result.error:
            for line in result.error.splitlines():
                print(f"      {line}")

        summary_path = test.path / "summary.txt"
        if summary_path.exists():
            print("   Perf summary:")
            for line in summary_path.read_text().splitlines():
                print(f"      {line}")

    passed = sum(1 for result in results if result.success)
    failed = len(results) - passed
    total_time = sum(result.duration for result in results)

    print()
    print("=" * 72)
    print(f"SUMMARY ({total_time:.1f}s)")
    print("=" * 72)
    print(f"  Passed: {passed}")
    print(f"  Failed: {failed}")

    if failed:
        print("\nFailed tests:")
        for result in results:
            if not result.success:
                print(f"  - {result.name}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
