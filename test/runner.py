#!/usr/bin/env python3
"""
Generic test runner for retrace record/replay tests.

Uses Docker for consistent record/replay behavior:
- Record phase: Docker with network access
- Replay phase: Docker with --network none (true isolation)

Usage:
    python runner.py <test_file.py> [options]
    
Examples:
    python runner.py otel.py
    python runner.py postgres.py --setup "docker run -d ..." --teardown "docker stop ..."

Test files can define optional setup/teardown functions:
    
    def setup():
        '''Called before record phase.'''
        # Start Docker containers, etc.
        
    def teardown():
        '''Called after record phase, before replay.'''
        # Stop containers, clean up resources
"""

import subprocess
import sys
import os
import shutil
import argparse
import importlib.util
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Tuple, Callable

# Docker image name for running tests
DOCKER_IMAGE = "retrace-test-runner"


@dataclass
class RunResult:
    exit_code: int
    stdout: str
    stderr: str


def run_command(cmd: list[str], capture: bool = True, env: Optional[dict] = None) -> RunResult:
    """Run a command and capture output."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    
    if capture:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            env=full_env
        )
        return RunResult(result.returncode, result.stdout, result.stderr)
    else:
        # Stream output to console
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=full_env
        )
        stdout, stderr = process.communicate()
        return RunResult(process.returncode, stdout, stderr)


def run_with_output(cmd: list[str], env: Optional[dict] = None) -> RunResult:
    """Run command, streaming to console AND capturing output."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=full_env
    )
    
    stdout_lines = []
    stderr_lines = []
    
    # Read both streams
    stdout, stderr = process.communicate()
    
    # Print to console
    if stdout:
        print(stdout, end='')
        stdout_lines.append(stdout)
    if stderr:
        print(stderr, end='', file=sys.stderr)
        stderr_lines.append(stderr)
    
    return RunResult(
        process.returncode,
        ''.join(stdout_lines),
        ''.join(stderr_lines)
    )


def get_python_cmd() -> str:
    """Get the Python executable path."""
    return sys.executable


def get_project_root() -> Path:
    """Get the project root directory (parent of test/)."""
    return Path(__file__).parent.parent.resolve()


def ensure_docker_image(verbose: bool = False) -> bool:
    """Build Docker image if it doesn't exist. Returns True if successful."""
    # Check if image exists
    result = subprocess.run(
        ['docker', 'image', 'inspect', DOCKER_IMAGE],
        capture_output=True
    )
    if result.returncode == 0:
        if verbose:
            print(f"   Docker image '{DOCKER_IMAGE}' already exists")
        return True
    
    print(f"🐳 Building Docker image '{DOCKER_IMAGE}'...")
    
    # Build image from Dockerfile
    # Note: We only install dependencies here. The actual source code
    # is mounted at runtime so changes are picked up without rebuilding.
    project_root = get_project_root()
    dockerfile_path = Path(__file__).parent / "Dockerfile"
    
    result = subprocess.run(
        ['docker', 'build', '-t', DOCKER_IMAGE, '-f', str(dockerfile_path), '.'],
        capture_output=True,
        cwd=project_root
    )
    
    if result.returncode != 0:
        print(f"❌ Failed to build Docker image:")
        print(result.stderr.decode())
        return False
    
    print(f"✅ Docker image '{DOCKER_IMAGE}' built successfully")
    return True


def run_test_directly(test_file: str) -> RunResult:
    """Run the test directly with Python (no retrace) to verify it works."""
    cmd = [get_python_cmd(), test_file]
    return run_with_output(cmd)


def run_in_docker(
    test_file: str,
    recording_dir: Path,
    mode: str,  # 'record' or 'replay'
    network: bool = True,
    verbose: bool = False
) -> RunResult:
    """Run test in Docker container."""
    project_root = get_project_root()
    test_file_path = Path(test_file).resolve()
    recording_dir = recording_dir.resolve()
    
    # Ensure recording dir exists
    recording_dir.mkdir(parents=True, exist_ok=True)
    
    # Build Docker command
    cmd = ['docker', 'run', '--rm']
    
    # Network mode
    if not network:
        cmd.extend(['--network', 'none'])
    else:
        cmd.extend(['--network', 'host'])
    
    # Mount project root
    cmd.extend(['-v', f'{project_root}:/app:ro'])
    
    # Mount recording directory (read-write for record, read-only for replay)
    if mode == 'record':
        cmd.extend(['-v', f'{recording_dir}:/recording:rw'])
    else:
        cmd.extend(['-v', f'{recording_dir}:/recording:ro'])
    
    # Working directory
    cmd.extend(['-w', '/app'])
    
    # Environment
    cmd.extend(['-e', 'PYTHONPATH=/app/src'])
    
    # Image
    cmd.append(DOCKER_IMAGE)
    
    # Python command
    if mode == 'record':
        test_rel_path = test_file_path.relative_to(project_root)
        docker_cmd = [
            'python', '-m', 'retracesoftware',
            '--recording', '/recording',
            '--', str(test_rel_path)
        ]
        if verbose:
            docker_cmd.insert(3, '--verbose')
    else:  # replay
        docker_cmd = [
            'python', '-m', 'retracesoftware',
            '--recording', '/recording'
        ]
        if verbose:
            docker_cmd.insert(3, '--verbose')
    
    cmd.extend(docker_cmd)
    
    return run_with_output(cmd)


def record_test(test_file: str, recording_dir: Path, verbose: bool = False) -> RunResult:
    """Run the test in record mode (in Docker with network)."""
    return run_in_docker(test_file, recording_dir, mode='record', network=True, verbose=verbose)


def replay_test(recording_dir: Path, test_file: str, verbose: bool = False) -> RunResult:
    """Run the test in replay mode (in Docker without network)."""
    return run_in_docker(test_file, recording_dir, mode='replay', network=False, verbose=verbose)


def compare_results(record: RunResult, replay: RunResult) -> Tuple[bool, list[str]]:
    """Compare record and replay results. Returns (success, differences)."""
    differences = []
    
    if record.exit_code != replay.exit_code:
        differences.append(
            f"Exit code mismatch: record={record.exit_code}, replay={replay.exit_code}"
        )
    
    if record.stdout != replay.stdout:
        differences.append("stdout mismatch")
        differences.append(f"  Record stdout:\n{indent(record.stdout)}")
        differences.append(f"  Replay stdout:\n{indent(replay.stdout)}")
    
    if record.stderr != replay.stderr:
        differences.append("stderr mismatch")
        differences.append(f"  Record stderr:\n{indent(record.stderr)}")
        differences.append(f"  Replay stderr:\n{indent(replay.stderr)}")
    
    return len(differences) == 0, differences


def indent(text: str, prefix: str = "    ") -> str:
    """Indent each line of text."""
    return '\n'.join(prefix + line for line in text.splitlines()) if text else f"{prefix}(empty)"


def run_shell_command(cmd: str) -> bool:
    """Run a shell command for setup/teardown."""
    if not cmd:
        return True
    print(f"  Running: {cmd}")
    result = subprocess.run(cmd, shell=True)
    return result.returncode == 0


@dataclass
class TestHooks:
    """Setup and teardown hooks for a test."""
    setup: Optional[Callable[[], None]] = None
    teardown: Optional[Callable[[], None]] = None
    setup_shell: Optional[str] = None
    teardown_shell: Optional[str] = None


def load_test_hooks(test_file: str) -> TestHooks:
    """Load setup/teardown functions from test file if they exist."""
    hooks = TestHooks()
    
    try:
        # Load the test module without executing it fully
        spec = importlib.util.spec_from_file_location("test_module", test_file)
        if spec is None or spec.loader is None:
            return hooks
        
        module = importlib.util.module_from_spec(spec)
        
        # We need to be careful here - we want to load the module to check
        # for setup/teardown, but not run the actual tests
        # Add the test directory to path temporarily
        test_dir = str(Path(test_file).parent)
        original_path = sys.path.copy()
        sys.path.insert(0, test_dir)
        
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            # Module might fail to import due to missing deps or other issues
            # That's okay - we'll just skip the hooks
            print(f"⚠️  Warning: Could not load test module for hooks: {e}")
            return hooks
        finally:
            sys.path = original_path
        
        # Check for setup/teardown functions
        if hasattr(module, 'setup') and callable(module.setup):
            hooks.setup = module.setup
        
        if hasattr(module, 'teardown') and callable(module.teardown):
            hooks.teardown = module.teardown
            
    except Exception as e:
        print(f"⚠️  Warning: Could not inspect test file for hooks: {e}")
    
    return hooks


def run_hooks(hooks: TestHooks, phase: str, shell_override: Optional[str] = None) -> bool:
    """Run setup or teardown hooks. Returns True on success."""
    if phase == 'setup':
        shell_cmd = shell_override or hooks.setup_shell
        func = hooks.setup
        label = "setup"
    else:
        shell_cmd = shell_override or hooks.teardown_shell
        func = hooks.teardown
        label = "teardown"
    
    # Shell command takes priority
    if shell_cmd:
        print(f"📦 Running {label} (shell)...")
        return run_shell_command(shell_cmd)
    
    # Then try Python function
    if func:
        print(f"📦 Running {label} (Python)...")
        try:
            func()
            print(f"  ✓ {label} completed")
            return True
        except Exception as e:
            print(f"  ✗ {label} failed: {e}")
            return False
    
    # No hooks defined
    return True


def main():
    parser = argparse.ArgumentParser(
        description='Run retrace record/replay test',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument('test_file', help='Python test file to run')
    parser.add_argument('--recording-dir', '-r', 
                        help='Directory for recording (default: ./recording_<testname>)')
    parser.add_argument('--output-dir', '-o',
                        help='Directory for output files (default: ./output_<testname>)')
    parser.add_argument('--setup', help='Shell command to run before record')
    parser.add_argument('--teardown', help='Shell command to run after record')
    parser.add_argument('--no-docker', action='store_true',
                        help='Run without Docker (uses native Python, no network isolation)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose output')
    parser.add_argument('--keep', '-k', action='store_true',
                        help='Keep recording and output directories after test')
    
    args = parser.parse_args()
    
    # Resolve test file path
    test_file = args.test_file
    if not os.path.isabs(test_file):
        test_file = os.path.abspath(test_file)
    
    if not os.path.exists(test_file):
        print(f"❌ Test file not found: {test_file}")
        sys.exit(1)
    
    test_name = Path(test_file).stem
    
    # Set up directories
    recording_dir = Path(args.recording_dir or f"./recording_{test_name}")
    output_dir = Path(args.output_dir or f"./output_{test_name}")
    
    # Clean up previous runs
    if recording_dir.exists():
        shutil.rmtree(recording_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"🧪 Running test: {test_name}")
    print(f"   Recording dir: {recording_dir}")
    print(f"   Output dir: {output_dir}")
    print()
    
    # Build Docker image (unless --no-docker)
    use_docker = not args.no_docker
    if use_docker:
        if not ensure_docker_image(args.verbose):
            print("❌ Failed to build Docker image")
            sys.exit(1)
        print()
    else:
        print("⚠️  Running without Docker (no network isolation for replay)")
        print()
    
    # Load hooks from test file
    hooks = load_test_hooks(test_file)
    has_hooks = hooks.setup or hooks.teardown
    if has_hooks:
        print(f"   Found hooks: setup={hooks.setup is not None}, teardown={hooks.teardown is not None}")
        print()
    
    # Setup (CLI arg overrides Python function)
    if args.setup or hooks.setup:
        if not run_hooks(hooks, 'setup', shell_override=args.setup):
            print("❌ Setup failed")
            sys.exit(1)
        print()
    
    # Preflight check - run test directly to verify it works
    print("🔍 PREFLIGHT CHECK (running without retrace)")
    print("=" * 50)
    preflight_result = run_test_directly(test_file)
    print("=" * 50)
    print(f"   Exit code: {preflight_result.exit_code}")
    print()
    
    if preflight_result.exit_code != 0:
        print("❌ PREFLIGHT FAILED - test doesn't work without retrace")
        print("   Fix the test before running through retrace.")
        if preflight_result.stderr:
            print("\n   stderr:")
            for line in preflight_result.stderr.splitlines()[:20]:
                print(f"      {line}")
        # Run teardown before exiting
        if args.teardown or hooks.teardown:
            run_hooks(hooks, 'teardown', shell_override=args.teardown)
        sys.exit(preflight_result.exit_code)
    
    print("✅ Preflight passed - test works without retrace")
    print()
    
    # Record phase
    print("🔴 RECORD PHASE" + (" (Docker)" if use_docker else " (native)"))
    print("=" * 50)
    if use_docker:
        record_result = record_test(test_file, recording_dir, args.verbose)
    else:
        # Native mode - run without Docker
        cmd = [
            get_python_cmd(), '-m', 'retracesoftware',
            '--recording', str(recording_dir),
            '--', test_file
        ]
        if args.verbose:
            cmd.insert(3, '--verbose')
        record_result = run_with_output(cmd)
    print("=" * 50)
    print(f"   Exit code: {record_result.exit_code}")
    print()
    
    # Save record output
    (output_dir / 'record_stdout.txt').write_text(record_result.stdout)
    (output_dir / 'record_stderr.txt').write_text(record_result.stderr)
    (output_dir / 'record_exitcode.txt').write_text(str(record_result.exit_code))
    
    # Teardown (run after record, before replay)
    if args.teardown or hooks.teardown:
        run_hooks(hooks, 'teardown', shell_override=args.teardown)
        print()
    
    # Check if record failed
    if record_result.exit_code != 0:
        print(f"❌ RECORD FAILED with exit code {record_result.exit_code}")
        sys.exit(record_result.exit_code)
    
    # Replay phase
    print("🔵 REPLAY PHASE" + (" (Docker --network none)" if use_docker else " (native, no isolation)"))
    print("=" * 50)
    if use_docker:
        replay_result = replay_test(recording_dir, test_file, args.verbose)
    else:
        # Native mode - run without Docker (no network isolation)
        cmd = [
            get_python_cmd(), '-m', 'retracesoftware',
            '--recording', str(recording_dir)
        ]
        if args.verbose:
            cmd.insert(3, '--verbose')
        replay_result = run_with_output(cmd)
    print("=" * 50)
    print(f"   Exit code: {replay_result.exit_code}")
    print()
    
    # Save replay output
    (output_dir / 'replay_stdout.txt').write_text(replay_result.stdout)
    (output_dir / 'replay_stderr.txt').write_text(replay_result.stderr)
    (output_dir / 'replay_exitcode.txt').write_text(str(replay_result.exit_code))
    
    # Compare
    print("📊 COMPARISON")
    print("=" * 50)
    success, differences = compare_results(record_result, replay_result)
    
    if success:
        print("✅ Exit codes match:", record_result.exit_code)
        print("✅ stdout matches")
        print("✅ stderr matches")
        print()
        print("🎉 SUCCESS: Record and replay outputs match!")
        exit_code = 0
    else:
        for diff in differences:
            print(f"❌ {diff}")
        print()
        print("💥 FAILURE: Outputs differ between record and replay")
        exit_code = 1
    
    # Cleanup
    if not args.keep:
        # Keep files for inspection on failure
        if not success:
            print(f"\n📁 Output saved to: {output_dir}")
    
    sys.exit(exit_code)


if __name__ == '__main__':
    main()
