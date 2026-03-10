import os
import sys

_REPO_MARKERS = ('meson.build', 'pyproject.toml', '.git')

# The Go replay binary lives inside this package directory.
# In a shipped wheel it is included as package data; during
# development it is built from source into the same location.
_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_BINARY = os.path.join(_PKG_DIR, 'replay')


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
    """Return the absolute path to the Go replay binary, building it if needed.

    The binary lives inside the retracesoftware package directory so that
    ``import retracesoftware`` is all that's needed to locate it.  In a
    shipped wheel the binary is included as package data.  During
    development, if the binary is missing it is built from the Go source
    tree into the same package-relative location.

    RETRACE_REPLAY_BIN overrides everything for unusual setups.
    """
    env_bin = os.environ.get('RETRACE_REPLAY_BIN')
    if env_bin and os.path.isfile(env_bin) and os.access(env_bin, os.X_OK):
        return env_bin

    if os.path.isfile(_BINARY) and os.access(_BINARY, os.X_OK):
        return _BINARY

    # Binary missing — try to build from source into the package dir
    repo_root = _find_repo_root(_PKG_DIR)
    if repo_root is None:
        raise FileNotFoundError(
            f"Go replay binary not found at {_BINARY} and cannot locate "
            f"repo root from {_PKG_DIR}; set RETRACE_REPLAY_BIN"
        )

    go_dir = _find_go_source(repo_root)
    if go_dir is None:
        raise FileNotFoundError(
            f"Go replay binary not found at {_BINARY} and cannot find "
            f"Go source (checked {repo_root}/go and "
            f"{os.path.dirname(repo_root)}/replay); "
            "set RETRACE_REPLAY_SRC or RETRACE_REPLAY_BIN"
        )

    import subprocess
    print(f"replay: building Go binary → {_BINARY}", file=sys.stderr)
    subprocess.check_call(['go', 'build', '-o', _BINARY, './cmd/replay'], cwd=go_dir)
    return _BINARY


def extract_binary_path() -> str:
    """Return the path to the binary for extraction (now unified into replay)."""
    return binary_path()


def _exec_replay():
    """Find (or build) the Go replay binary and exec it."""
    go_bin = binary_path()
    os.execvp(go_bin, [go_bin] + sys.argv[1:])
