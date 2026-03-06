import os
import sys


def binary_path() -> str:
    """Return the absolute path to the Go replay binary, building it if needed."""
    pkg_dir = os.path.dirname(os.path.abspath(__file__))

    # Packaged binary (inside installed wheel)
    go_bin = os.path.join(pkg_dir, 'replay')
    if os.path.isfile(go_bin) and os.access(go_bin, os.X_OK):
        return go_bin

    # Dev build: compile from source if needed
    repo_root = os.path.normpath(os.path.join(pkg_dir, '..', '..', '..'))
    go_dir = os.path.join(repo_root, 'go')
    go_bin = os.path.join(repo_root, '_build', 'replay')

    if not os.path.isfile(go_bin):
        import subprocess
        os.makedirs(os.path.dirname(go_bin), exist_ok=True)
        print(f"replay: building Go binary → {go_bin}", file=sys.stderr)
        subprocess.check_call(['go', 'build', '-o', go_bin, './cmd/replay'], cwd=go_dir)

    return go_bin


def extract_binary_path() -> str:
    """Return the path to the binary for extraction (now unified into replay)."""
    return binary_path()


def _exec_replay():
    """Find (or build) the Go replay binary and exec it."""
    go_bin = binary_path()
    os.execvp(go_bin, [go_bin] + sys.argv[1:])
