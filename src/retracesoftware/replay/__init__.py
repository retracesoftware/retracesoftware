import os
import sys

_REPO_MARKERS = ('meson.build', 'pyproject.toml', '.git')


def _find_repo_root(start: str) -> str | None:
    """Walk up from *start* looking for a directory that contains a repo marker."""
    d = os.path.abspath(start)
    while True:
        if any(os.path.exists(os.path.join(d, m)) for m in _REPO_MARKERS):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def _find_go_source(repo_root: str) -> str | None:
    """Locate the Go replay source tree (contains cmd/replay/main.go)."""
    candidates = [
        os.environ.get('RETRACE_REPLAY_SRC', ''),
        os.path.join(repo_root, 'go'),
        os.path.join(os.path.dirname(repo_root), 'replay'),
    ]
    for d in candidates:
        if d and os.path.isfile(os.path.join(d, 'cmd', 'replay', 'main.go')):
            return d
    return None


def binary_path() -> str:
    """Return the absolute path to the Go replay binary, building it if needed."""
    env_bin = os.environ.get('RETRACE_REPLAY_BIN')
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin

    pkg_dir = os.path.dirname(os.path.abspath(__file__))

    # Packaged binary (inside installed wheel)
    go_bin = os.path.join(pkg_dir, 'replay')
    if os.path.isfile(go_bin) and os.access(go_bin, os.X_OK):
        return go_bin

    # Dev build: find repo root by walking up from this file
    repo_root = _find_repo_root(pkg_dir)
    if repo_root is None:
        raise FileNotFoundError(
            "Cannot locate retracesoftware repo root from "
            f"{pkg_dir}; set RETRACE_REPLAY_BIN to the replay binary path"
        )

    go_bin = os.path.join(repo_root, '_build', 'replay')
    if os.path.isfile(go_bin) and os.access(go_bin, os.X_OK):
        return go_bin

    # Binary doesn't exist yet — try to build from source
    go_dir = _find_go_source(repo_root)
    if go_dir is None:
        raise FileNotFoundError(
            f"Cannot find Go replay source (checked {repo_root}/go and "
            f"{os.path.dirname(repo_root)}/replay); "
            "set RETRACE_REPLAY_SRC or RETRACE_REPLAY_BIN"
        )

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
