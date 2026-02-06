#!/usr/bin/env python3
"""
Test suite runner - discovers and runs all retrace tests.

Usage:
    python run_all.py                    # Run all tests in tests/
    python run_all.py opentelemetry psycopg2  # Run specific tests
    python run_all.py --tags fast        # Run only tests tagged "fast"
    python run_all.py -e slow docker     # Exclude slow/docker tests
    python run_all.py --list             # List tests with metadata
    python run_all.py --list-tags        # List all available tags

Test metadata is defined in test files (tests/*.py):
    TAGS = ["slow", "network"]      # For filtering
    TIMEOUT = 120                   # Seconds (default: 300)
    SKIP = True                     # Skip this test
    SKIP_REASON = "Needs API key"   # Why it's skipped
"""

import subprocess
import sys
import argparse
import ast
import time
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed



@dataclass
class TestConfig:
    """Configuration for a single test."""
    file: str
    setup: Optional[str] = None
    teardown: Optional[str] = None
    timeout: int = 300  # 5 minutes default
    skip: bool = False
    skip_reason: str = ""
    no_network_block: bool = False
    tags: list[str] = field(default_factory=list)


@dataclass
class TestResult:
    """Result of running a single test."""
    name: str
    success: bool
    duration: float
    exit_code: int
    error: str = ""
    skipped: bool = False
    skip_reason: str = ""


def load_all_configs(test_files: list[Path]) -> dict[str, TestConfig]:
    """Load test configurations from test files."""
    configs = {}
    for test_file in test_files:
        name = test_file.stem
        configs[name] = load_config_from_file(test_file)
    return configs


def extract_test_metadata(test_file: Path) -> dict:
    """
    Extract metadata from a test file by parsing module-level assignments.
    
    Looks for:
        TAGS = ["tag1", "tag2"]
        TIMEOUT = 120
        SKIP = True
        SKIP_REASON = "reason"
    
    Returns dict with found values.
    """
    metadata = {}
    
    try:
        source = test_file.read_text()
        tree = ast.parse(source)
        
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name = target.id
                        if name in ('TAGS', 'TIMEOUT', 'SKIP', 'SKIP_REASON'):
                            try:
                                metadata[name] = ast.literal_eval(node.value)
                            except (ValueError, TypeError):
                                # Can't evaluate, skip
                                pass
    except (SyntaxError, FileNotFoundError):
        pass
    
    return metadata


def load_config_from_file(test_file: Path) -> TestConfig:
    """Load test configuration from the test file itself."""
    metadata = extract_test_metadata(test_file)
    
    tags = metadata.get('TAGS', [])
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(',') if t.strip()]
    
    return TestConfig(
        file=test_file.name,
        timeout=metadata.get('TIMEOUT', 300),
        skip=metadata.get('SKIP', False),
        skip_reason=metadata.get('SKIP_REASON', ''),
        tags=tags,
    )


def discover_tests(pattern: str = "*.py", test_dir: Path = None) -> list[Path]:
    """Discover test files matching pattern."""
    if test_dir is None:
        test_dir = Path(__file__).parent / "tests"
    
    if not test_dir.exists():
        return []
    
    tests = sorted(test_dir.glob(pattern))
    return [t for t in tests if t.is_file() and t.name != '__init__.py']


def filter_tests_by_tags(
    test_files: list[Path],
    configs: dict[str, TestConfig],
    include_tags: list[str] = None,
    exclude_tags: list[str] = None,
    all_tags: list[str] = None,
) -> list[Path]:
    """
    Filter tests by tags.
    
    Args:
        test_files: List of test file paths
        configs: Test configurations from tests.toml
        include_tags: Include tests with ANY of these tags (OR logic)
        exclude_tags: Exclude tests with ANY of these tags
        all_tags: Include only tests with ALL of these tags (AND logic)
    
    Returns:
        Filtered list of test files
    """
    if not include_tags and not exclude_tags and not all_tags:
        return test_files
    
    filtered = []
    for test_file in test_files:
        name = test_file.stem
        config = configs.get(name)
        test_tags = set(config.tags) if config else set()
        
        # Check exclude tags first (takes precedence)
        if exclude_tags and test_tags & set(exclude_tags):
            continue
        
        # Check all_tags (AND logic)
        if all_tags and not set(all_tags).issubset(test_tags):
            continue
        
        # Check include tags (OR logic)
        if include_tags and not (test_tags & set(include_tags)):
            continue
        
        filtered.append(test_file)
    
    return filtered


def get_all_tags(configs: dict[str, TestConfig]) -> dict[str, list[str]]:
    """Get all unique tags and which tests use them."""
    tag_to_tests: dict[str, list[str]] = {}
    for name, config in configs.items():
        for tag in config.tags:
            if tag not in tag_to_tests:
                tag_to_tests[tag] = []
            tag_to_tests[tag].append(name)
    return tag_to_tests


def run_single_test(test_file: Path, config: Optional[TestConfig] = None, 
                    verbose: bool = False) -> TestResult:
    """Run a single test through runner.py."""
    name = test_file.stem
    start_time = time.time()
    
    # Check if skipped
    if config and config.skip:
        return TestResult(
            name=name,
            success=True,
            duration=0,
            exit_code=0,
            skipped=True,
            skip_reason=config.skip_reason or "Skipped in config"
        )
    
    # Build command
    runner_path = Path(__file__).parent / 'runner.py'
    cmd = [sys.executable, str(runner_path), str(test_file)]
    
    if config:
        if config.setup:
            cmd.extend(['--setup', config.setup])
        if config.teardown:
            cmd.extend(['--teardown', config.teardown])
        if config.no_network_block:
            cmd.append('--no-network-block')
    
    if verbose:
        cmd.append('--verbose')
    
    # Run test
    try:
        timeout = config.timeout if config else 300
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=test_file.parent
        )
        duration = time.time() - start_time
        
        return TestResult(
            name=name,
            success=result.returncode == 0,
            duration=duration,
            exit_code=result.returncode,
            error=result.stderr if result.returncode != 0 else ""
        )
    
    except subprocess.TimeoutExpired:
        duration = time.time() - start_time
        return TestResult(
            name=name,
            success=False,
            duration=duration,
            exit_code=-1,
            error=f"Test timed out after {timeout}s"
        )
    
    except Exception as e:
        duration = time.time() - start_time
        return TestResult(
            name=name,
            success=False,
            duration=duration,
            exit_code=-1,
            error=str(e)
        )


def print_result(result: TestResult, verbose: bool = False):
    """Print a single test result."""
    if result.skipped:
        print(f"  ⏭️  {result.name}: SKIPPED ({result.skip_reason})")
    elif result.success:
        print(f"  ✅ {result.name}: PASSED ({result.duration:.2f}s)")
    else:
        print(f"  ❌ {result.name}: FAILED ({result.duration:.2f}s)")
        if verbose and result.error:
            for line in result.error.splitlines()[:40]:
                print(f"      {line}")


def print_summary(results: list[TestResult]):
    """Print summary of all test results."""
    passed = sum(1 for r in results if r.success and not r.skipped)
    failed = sum(1 for r in results if not r.success and not r.skipped)
    skipped = sum(1 for r in results if r.skipped)
    total_time = sum(r.duration for r in results)
    
    print()
    print("=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"  Total:   {len(results)} tests")
    print(f"  Passed:  {passed} ✅")
    print(f"  Failed:  {failed} ❌")
    print(f"  Skipped: {skipped} ⏭️")
    print(f"  Time:    {total_time:.2f}s")
    print()
    
    if failed > 0:
        print("Failed tests:")
        for r in results:
            if not r.success and not r.skipped:
                print(f"  - {r.name}")
                if r.error:
                    # Print first line of error
                    first_line = r.error.splitlines()[0] if r.error else ""
                    print(f"    {first_line[:80]}")
        print()
    
    return failed == 0


def main():
    parser = argparse.ArgumentParser(
        description='Run all retrace tests',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('tests', nargs='*', 
                        help='Specific test names to run (without _test.py suffix)')
    parser.add_argument('--pattern', '-p', default='*.py',
                        help='Glob pattern for test discovery (default: *.py)')
    parser.add_argument('--parallel', '-j', type=int, default=1,
                        help='Number of tests to run in parallel')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Verbose output')
    parser.add_argument('--list', '-l', action='store_true',
                        help='List tests without running')
    parser.add_argument('--fail-fast', '-x', action='store_true',
                        help='Stop on first failure')
    parser.add_argument('--tags', '-t', nargs='+', default=[],
                        help='Only run tests with ANY of these tags (OR logic)')
    parser.add_argument('--exclude-tags', '-e', nargs='+', default=[],
                        help='Skip tests with ANY of these tags')
    parser.add_argument('--all-tags', nargs='+', default=[],
                        help='Only run tests with ALL of these tags (AND logic)')
    parser.add_argument('--list-tags', action='store_true',
                        help='List all available tags and exit')
    
    args = parser.parse_args()
    
    test_dir = Path(__file__).parent / "tests"
    
    # Discover tests first (needed to extract file-based metadata)
    if args.tests:
        # Run specific tests
        test_files = []
        for name in args.tests:
            # Try exact match first
            test_file = test_dir / f"{name}_test.py"
            if not test_file.exists():
                test_file = test_dir / f"{name}.py"
            if not test_file.exists():
                test_file = test_dir / name
            
            if test_file.exists():
                test_files.append(test_file)
            else:
                print(f"⚠️  Test not found: {name}")
    else:
        test_files = discover_tests(args.pattern, test_dir)
    
    if not test_files:
        print("No tests found!")
        sys.exit(1)
    
    # Load configs from test files
    configs = load_all_configs(test_files)
    
    # List tags mode
    if args.list_tags:
        all_tags = get_all_tags(configs)
        if not all_tags:
            print("No tags defined.")
            print("\nTo add tags, add to your test file:")
            print('  TAGS = ["slow", "network"]')
        else:
            print("Available tags:")
            for tag, tests in sorted(all_tags.items()):
                print(f"  {tag}: {', '.join(tests)}")
        sys.exit(0)
    
    # Apply tag filters
    if args.tags or args.exclude_tags or args.all_tags:
        original_count = len(test_files)
        test_files = filter_tests_by_tags(
            test_files, configs,
            include_tags=args.tags,
            exclude_tags=args.exclude_tags,
            all_tags=args.all_tags,
        )
        if original_count != len(test_files):
            print(f"🏷️  Tag filter: {original_count} → {len(test_files)} tests")
        if not test_files:
            print("No tests match the tag filter!")
            sys.exit(1)
    
    print(f"🧪 Found {len(test_files)} test(s)")
    print()
    
    # List mode
    if args.list:
        for test_file in test_files:
            name = test_file.stem
            config = configs.get(name)
            parts = []
            if config:
                if config.skip:
                    parts.append(f"skip: {config.skip_reason}")
                if config.setup:
                    parts.append("has setup")
                if config.tags:
                    parts.append(f"tags: {', '.join(config.tags)}")
            status = f" ({'; '.join(parts)})" if parts else ""
            print(f"  - {name}{status}")
        sys.exit(0)
    
    # Run tests
    print("Running tests...")
    print("-" * 60)
    
    results: list[TestResult] = []
    
    if args.parallel > 1:
        # Parallel execution
        with ThreadPoolExecutor(max_workers=args.parallel) as executor:
            futures = {}
            for test_file in test_files:
                name = test_file.stem
                config = configs.get(name)
                future = executor.submit(run_single_test, test_file, config, args.verbose)
                futures[future] = test_file
            
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                print_result(result, args.verbose)
                
                if args.fail_fast and not result.success and not result.skipped:
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
    else:
        # Sequential execution
        for test_file in test_files:
            name = test_file.stem
            config = configs.get(name)
            
            print(f"\n🔬 Running: {test_file.name}")
            result = run_single_test(test_file, config, args.verbose)
            results.append(result)
            print_result(result, args.verbose)
            
            if args.fail_fast and not result.success and not result.skipped:
                break
    
    # Print summary
    success = print_summary(results)
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
