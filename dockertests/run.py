# #!/usr/bin/env python3
# """
# Run all retrace docker tests.

# Discovers tests in tests/ directory and runs each via runtest.sh.

# Usage:
#     python run.py                     # Run all tests
#     python run.py postgres_test       # Run specific test
#     python run.py --list              # List available tests
#     python run.py --tags db,slow      # Run tests with specific tags
#     python run.py --image python:3.12 # Use specific Python image

# Each test can have a 'tags' file with one tag per line:
#     db
#     slow
#     network
# """

# import subprocess
# import sys
# import time
# from dataclasses import dataclass, field
# from pathlib import Path


# @dataclass
# class TestInfo:
#     path: Path
#     name: str
#     tags: list[str] = field(default_factory=list)
#     has_compose: bool = False
#     has_requirements: bool = False


# @dataclass
# class TestResult:
#     name: str
#     success: bool
#     duration: float
#     error: str = ""


# def load_tags(test_dir: Path) -> list[str]:
#     """Load tags from 'tags' file (one tag per line)."""
#     tags_file = test_dir / "tags"
#     if not tags_file.exists():
#         return []
    
#     try:
#         with open(tags_file) as f:
#             return [line.strip() for line in f if line.strip() and not line.startswith('#')]
#     except Exception:
#         return []


# def discover_tests() -> list[TestInfo]:
#     """Find all test directories containing test.py."""
#     tests_dir = Path(__file__).parent / "tests"
#     if not tests_dir.exists():
#         return []

#     tests = []
#     for entry in sorted(tests_dir.iterdir()):
#         if entry.is_dir() and (entry / "test.py").exists():
#             tests.append(TestInfo(
#                 path=entry,
#                 name=entry.name,
#                 tags=load_tags(entry),
#                 has_compose=(entry / "docker-compose.yml").exists(),
#                 has_requirements=(entry / "requirements.txt").exists(),
#             ))
#     return tests


# def run_test(test_name: str, image: str | None = None) -> TestResult:
#     """Run a single test via runtest.sh."""
#     script = Path(__file__).parent / "runtest.sh"
    
#     cmd = [str(script), test_name]
#     if image:
#         cmd.append(image)
    
#     start = time.time()
#     try:
#         result = subprocess.run(
#             cmd,
#             cwd=script.parent,
#             capture_output=True,
#             text=True
#         )
#         duration = time.time() - start
        
#         if result.returncode == 0:
#             return TestResult(name=test_name, success=True, duration=duration)
#         else:
#             # Get last few lines of output for error context
#             output = (result.stdout + result.stderr).strip()
#             error_lines = output.split('\n')[-5:]
#             error = '\n'.join(error_lines)
#             return TestResult(name=test_name, success=False, duration=duration, error=error)
    
#     except Exception as e:
#         return TestResult(
#             name=test_name,
#             success=False,
#             duration=time.time() - start,
#             error=str(e)
#         )


# def filter_by_tags(tests: list[TestInfo], tags: list[str]) -> list[TestInfo]:
#     """Filter tests that have at least one of the specified tags."""
#     tag_set = set(tags)
#     return [t for t in tests if tag_set & set(t.tags)]


# def main():
#     import argparse
    
#     parser = argparse.ArgumentParser(description='Run retrace docker tests')
#     parser.add_argument('tests', nargs='*', help='Specific tests to run')
#     parser.add_argument('--list', '-l', action='store_true', help='List available tests')
#     parser.add_argument('--tags', '-t', help='Run tests with these tags (comma-separated)')
#     parser.add_argument('--image', '-i', help='Docker image to use (default: python:3.11-slim)')
    
#     args = parser.parse_args()
    
#     # Discover tests
#     all_tests = discover_tests()
    
#     if not all_tests:
#         print("No tests found in dockertests/tests/")
#         print("Each test should be a directory containing test.py")
#         sys.exit(1)
    
#     # List mode
#     if args.list:
#         print(f"Found {len(all_tests)} test(s):")
#         for test in all_tests:
#             extras = []
#             if test.tags:
#                 extras.append(f"tags: {', '.join(test.tags)}")
#             if test.has_compose:
#                 extras.append("docker-compose.yml")
#             if test.has_requirements:
#                 extras.append("requirements.txt")
#             suffix = f" ({'; '.join(extras)})" if extras else ""
#             print(f"  - {test.name}{suffix}")
#         sys.exit(0)
    
#     # Filter tests if specific ones requested
#     if args.tests:
#         test_names = set(args.tests)
#         tests_to_run = [t for t in all_tests if t.name in test_names]
#         not_found = test_names - {t.name for t in tests_to_run}
#         for name in not_found:
#             print(f"⚠️  Test not found: {name}")
#     else:
#         tests_to_run = all_tests
    
#     # Filter by tags
#     if args.tags:
#         tags = [t.strip() for t in args.tags.split(',')]
#         tests_to_run = filter_by_tags(tests_to_run, tags)
#         if not tests_to_run:
#             print(f"No tests found with tags: {', '.join(tags)}")
#             sys.exit(0)
    
#     if not tests_to_run:
#         print("No tests to run!")
#         sys.exit(1)
    
#     # Run tests
#     print(f"🧪 Running {len(tests_to_run)} test(s)")
#     if args.tags:
#         print(f"   Tags: {args.tags}")
#     if args.image:
#         print(f"   Image: {args.image}")
#     print("=" * 60)
    
#     results = []
#     for test in tests_to_run:
#         print(f"\n🔬 {test.name}...")
        
#         result = run_test(test.name, args.image)
#         results.append(result)
        
#         if result.success:
#             print(f"   ✅ PASSED ({result.duration:.1f}s)")
#         else:
#             print(f"   ❌ FAILED ({result.duration:.1f}s)")
#             if result.error:
#                 # Indent error output
#                 for line in result.error.split('\n'):
#                     print(f"      {line}")
    
#     # Summary
#     passed = sum(1 for r in results if r.success)
#     failed = len(results) - passed
#     total_time = sum(r.duration for r in results)
    
#     print()
#     print("=" * 60)
#     print(f"SUMMARY ({total_time:.1f}s)")
#     print("=" * 60)
#     print(f"  Passed: {passed} ✅")
#     print(f"  Failed: {failed} ❌")
    
#     if failed > 0:
#         print("\nFailed tests:")
#         for r in results:
#             if not r.success:
#                 print(f"  - {r.name}")
    
#     sys.exit(0 if failed == 0 else 1)


# if __name__ == '__main__':
#     main()

#!/usr/bin/env python3
"""
Run all retrace docker tests.

Discovers tests in tests/ directory and runs each via runtest.sh.

Usage:
    python run.py                           # Run all tests (excludes perf tests)
    python run.py postgres_test             # Run specific test(s)
    python run.py --list                    # List available tests
    python run.py --tags db,slow            # Run tests with specific tags
    python run.py --tags perf               # Run only perf tests
    python run.py --include-perf            # Include perf tests in run
    python run.py --image python:3.12       # Use specific Python image
    python run.py --exclude asgiref_test    # Exclude tests by name
    python run.py -x asgiref_test -x py_test # Exclude tests (repeatable)

Each test can have a 'tags' file with one tag per line:
    db
    slow
    network
    perf  # Excluded by default (use --include-perf or --tags perf)
"""

import subprocess
import sys
import time
import shutil
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class TestInfo:
    path: Path
    name: str
    tags: list[str] = field(default_factory=list)
    has_compose: bool = False
    has_requirements: bool = False


@dataclass
class TestResult:
    name: str
    success: bool
    duration: float
    error: str = ""


def load_tags(test_dir: Path) -> list[str]:
    """Load tags from 'tags' file (one tag per line)."""
    tags_file = test_dir / "tags"
    if not tags_file.exists():
        return []

    try:
        with open(tags_file) as f:
            return [line.strip() for line in f if line.strip() and not line.startswith('#')]
    except Exception:
        return []


def discover_tests() -> list[TestInfo]:
    """Find all test directories containing test.py."""
    tests_dir = Path(__file__).parent / "tests"
    if not tests_dir.exists():
        return []

    tests = []
    for entry in sorted(tests_dir.iterdir()):
        if entry.is_dir() and (entry / "test.py").exists():
            tests.append(TestInfo(
                path=entry,
                name=entry.name,
                tags=load_tags(entry),
                has_compose=(entry / "docker-compose.yml").exists(),
                has_requirements=(entry / "requirements.txt").exists(),
            ))
    return tests


def run_test(test_name: str, image: str | None = None) -> TestResult:
    """Run a single test via runtest.sh."""
    script = Path(__file__).parent / "runtest.sh"

    cmd = [str(script), test_name]
    if image:
        cmd.extend(["--image", image])

    start = time.time()
    try:
        result = subprocess.run(
            cmd,
            cwd=script.parent,
            text=True
        )
        duration = time.time() - start

        if result.returncode == 0:
            return TestResult(name=test_name, success=True, duration=duration)
        else:
            error = f"runtest.sh exited with code {result.returncode}"
            return TestResult(name=test_name, success=False, duration=duration, error=error)

    except Exception as e:
        return TestResult(
            name=test_name,
            success=False,
            duration=time.time() - start,
            error=str(e)
        )


def filter_by_tags(tests: list[TestInfo], tags: list[str]) -> list[TestInfo]:
    """Filter tests that have at least one of the specified tags."""
    tag_set = set(tags)
    return [t for t in tests if tag_set & set(t.tags)]


def parse_excludes(exclude_args: list[str]) -> set[str]:
    """
    Supports both:
      - repeated flags: -x a -x b
      - comma-separated: --exclude a,b
    """
    excluded: set[str] = set()
    for item in exclude_args:
        if not item:
            continue
        parts = [p.strip() for p in item.split(",")]
        for p in parts:
            if p:
                excluded.add(p)
    return excluded


def clean_harness_state(dockertests_dir: Path) -> None:
    """Remove cached package state and cleanup stale harness docker artifacts."""
    print("🧹 Cleaning harness state...")

    cache_dirs = [
        dockertests_dir / ".cache" / "packages",
        dockertests_dir / ".cache" / "packages-debug",
        dockertests_dir / ".cache" / "pip",
    ]
    for path in cache_dirs:
        if path.exists():
            shutil.rmtree(path)
            print(f"   Removed: {path}")

    # Best-effort docker cleanup for stale harness containers/networks.
    # Keep this non-fatal so users can still run tests without docker available.
    try:
        container_ids_cmd = [
            "docker", "ps", "-aq", "--filter", "label=com.docker.compose.project"
        ]
        container_ids = subprocess.run(
            container_ids_cmd, capture_output=True, text=True, check=False
        ).stdout.split()

        stale_containers: list[str] = []
        if container_ids:
            inspected = subprocess.run(
                ["docker", "inspect", *container_ids],
                capture_output=True,
                text=True,
                check=False,
            )
            if inspected.returncode == 0 and inspected.stdout.strip():
                for item in json.loads(inspected.stdout):
                    project = (
                        (item.get("Config") or {}).get("Labels") or {}
                    ).get("com.docker.compose.project", "")
                    cid = item.get("Id", "")
                    if project.startswith("retracetest_") and cid:
                        stale_containers.append(cid)
        if stale_containers:
            subprocess.run(
                ["docker", "rm", "-f", *stale_containers],
                capture_output=True,
                text=True,
                check=False,
            )
            print(f"   Removed stale containers: {len(stale_containers)}")

        network_ids_cmd = [
            "docker", "network", "ls", "-q", "--filter", "label=com.docker.compose.project"
        ]
        network_ids = subprocess.run(
            network_ids_cmd, capture_output=True, text=True, check=False
        ).stdout.split()

        stale_networks: list[str] = []
        if network_ids:
            inspected = subprocess.run(
                ["docker", "network", "inspect", *network_ids],
                capture_output=True,
                text=True,
                check=False,
            )
            if inspected.returncode == 0 and inspected.stdout.strip():
                for item in json.loads(inspected.stdout):
                    project = ((item.get("Labels") or {}).get("com.docker.compose.project", ""))
                    nid = item.get("Id", "")
                    if project.startswith("retracetest_") and nid:
                        stale_networks.append(nid)
        if stale_networks:
            subprocess.run(
                ["docker", "network", "rm", *stale_networks],
                capture_output=True,
                text=True,
                check=False,
            )
            print(f"   Removed stale networks: {len(stale_networks)}")
    except Exception as exc:
        print(f"   ⚠️ Docker cleanup skipped: {exc}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Run retrace docker tests')
    parser.add_argument('tests', nargs='*', help='Specific tests to run')
    parser.add_argument('--list', '-l', action='store_true', help='List available tests')
    parser.add_argument('--tags', '-t', help='Run tests with these tags (comma-separated)')
    parser.add_argument('--image', '-i', help='Docker image to use (default: python:3.11-slim)')
    parser.add_argument(
        '--exclude', '-x',
        action='append',
        default=[],
        help='Exclude tests by name (repeatable or comma-separated). Example: -x asgiref_test -x astroid_test OR --exclude asgiref_test,astroid_test'
    )
    parser.add_argument(
        '--include-perf',
        action='store_true',
        help='Include perf-tagged tests (excluded by default because they take a long time)'
    )
    parser.add_argument(
        '--clean',
        action='store_true',
        help='Clean harness caches/orphan compose artifacts before running tests'
    )

    args = parser.parse_args()

    dockertests_dir = Path(__file__).parent
    if args.clean:
        clean_harness_state(dockertests_dir)

    # Discover tests
    all_tests = discover_tests()

    if not all_tests:
        print("No tests found in dockertests/tests/")
        print("Each test should be a directory containing test.py")
        sys.exit(1)

    # List mode
    if args.list:
        print(f"Found {len(all_tests)} test(s):")
        for test in all_tests:
            extras = []
            if test.tags:
                extras.append(f"tags: {', '.join(test.tags)}")
            if test.has_compose:
                extras.append("docker-compose.yml")
            if test.has_requirements:
                extras.append("requirements.txt")
            suffix = f" ({'; '.join(extras)})" if extras else ""
            print(f"  - {test.name}{suffix}")
        sys.exit(0)

    # Filter tests if specific ones requested
    if args.tests:
        test_names = set(args.tests)
        tests_to_run = [t for t in all_tests if t.name in test_names]
        not_found = test_names - {t.name for t in tests_to_run}
        for name in sorted(not_found):
            print(f"⚠️  Test not found: {name}")
    else:
        tests_to_run = all_tests

    # Filter by tags
    if args.tags:
        tags = [t.strip() for t in args.tags.split(',') if t.strip()]
        tests_to_run = filter_by_tags(tests_to_run, tags)
        if not tests_to_run:
            print(f"No tests found with tags: {', '.join(tags)}")
            sys.exit(0)

    # Exclude perf tests by default (they take forever)
    # Include them only if: --include-perf, --tags perf, or explicitly named
    requested_tags = {t.strip() for t in (args.tags or '').split(',') if t.strip()}
    requested_by_name = set(args.tests) if args.tests else set()
    if not args.include_perf and 'perf' not in requested_tags:
        perf_excluded = [t.name for t in tests_to_run if 'perf' in t.tags and t.name not in requested_by_name]
        if perf_excluded:
            print(f"ℹ️  Excluding perf tests (use --include-perf or --tags perf to run them): {', '.join(perf_excluded)}")
        tests_to_run = [t for t in tests_to_run if 'perf' not in t.tags or t.name in requested_by_name]

    # Exclude tests
    excluded = parse_excludes(args.exclude)
    if excluded:
        available = {t.name for t in all_tests}
        unknown = sorted(excluded - available)
        for name in unknown:
            print(f"⚠️  Excluded test not found: {name}")

        tests_to_run = [t for t in tests_to_run if t.name not in excluded]

    if not tests_to_run:
        print("No tests to run!")
        sys.exit(1)

    # Run tests
    print(f"🧪 Running {len(tests_to_run)} test(s)")
    if args.tags:
        print(f"   Tags: {args.tags}")
    if excluded:
        print(f"   Excluding: {', '.join(sorted(excluded))}")
    if args.image:
        print(f"   Image: {args.image}")
    print("=" * 60)

    results = []
    for test in tests_to_run:
        print(f"\n🔬 {test.name}...")

        result = run_test(test.name, args.image)
        results.append(result)

        if result.success:
            print(f"   ✅ PASSED ({result.duration:.1f}s)")
        else:
            print(f"   ❌ FAILED ({result.duration:.1f}s)")
            if result.error:
                for line in result.error.split('\n'):
                    print(f"      {line}")

        summary_path = test.path / "summary.txt"
        if summary_path.exists():
            print("   📊 Perf summary:")
            for line in summary_path.read_text().splitlines():
                print(f"      {line}")

    # Summary
    passed = sum(1 for r in results if r.success)
    failed = len(results) - passed
    total_time = sum(r.duration for r in results)

    print()
    print("=" * 60)
    print(f"SUMMARY ({total_time:.1f}s)")
    print("=" * 60)
    print(f"  Passed: {passed} ✅")
    print(f"  Failed: {failed} ❌")

    if failed > 0:
        print("\nFailed tests:")
        for r in results:
            if not r.success:
                print(f"  - {r.name}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == '__main__':
    main()
