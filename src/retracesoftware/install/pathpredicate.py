"""Path predicate for deciding which filesystem calls to retrace vs passthrough.

The predicate returns ``True`` when a call should be **retraced** (proxied
for record/replay) and ``False`` when it should **passthrough** to the real
OS function.

Rules:
- Integer arguments (file descriptors) → always retrace.
- String / path-like arguments → ``str(arg)`` tested against an inclusive
  set of regex patterns.  Any match → retrace; no match → passthrough.
"""

import re
import sys
import pkgutil
import fnmatch
import os
import site
import sysconfig


def _ignore_globs_for_root(root):
    roots = {
        os.fsdecode(root),
        os.path.realpath(root),
    }
    globs = []
    for item in sorted(roots):
        item = item.replace("\\", "/").rstrip("/")
        if not item:
            continue
        globs.extend((item, f"{item}/*"))
    return tuple(globs)


def _package_root_ignore_globs():
    package_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots = [package_root]

    # Editable installs commonly import retracesoftware from
    # <checkout>/src/retracesoftware while importlib/path hooks stat the
    # checkout root itself. Treat that source checkout as Retrace control-plane
    # input too, but do not broaden wheel installs under site-packages.
    source_root = os.path.dirname(package_root)
    checkout_root = os.path.dirname(source_root)
    if os.path.basename(source_root) == "src":
        roots.extend([source_root, checkout_root])

    globs = []
    for root in roots:
        globs.extend(_ignore_globs_for_root(root))
    return tuple(globs)


def _library_root_ignore_globs():
    roots = {
        sys.prefix,
        sys.exec_prefix,
    }
    for key in ("purelib", "platlib"):
        path = sysconfig.get_path(key)
        if path:
            roots.add(path)
    for getter in (site.getsitepackages, site.getusersitepackages):
        try:
            paths = getter()
        except Exception:
            continue
        if isinstance(paths, (str, bytes, os.PathLike)):
            roots.add(paths)
        else:
            roots.update(path for path in paths if path)

    globs = []
    for root in sorted(roots):
        globs.extend(_ignore_globs_for_root(root))
    return tuple(globs)


DEFAULT_IGNORE_GLOBS = (
    "__pycache__",
    "__pycache__/*",
    "*/__pycache__",
    "*/__pycache__/*",
    # Pytest tmp_path roots are framework scratch space. Half-recording their
    # directory setup leaves high-level file opens without live parent dirs.
    "/tmp/pytest-of-*",
    "/tmp/pytest-of-*/*",
    "/var/tmp/pytest-of-*",
    "/var/tmp/pytest-of-*/*",
    # Retrace's own package files are control-plane inputs. If an editable
    # checkout or venv lives under /tmp, the broad /tmp retrace pattern should
    # not turn import/resource/stat probes for retracesoftware itself into
    # recorded application behavior.
    *_package_root_ignore_globs(),
    # Virtualenv/site-package contents are dependency and interpreter inputs.
    # They normally live outside /tmp and pass through; keep that behavior when
    # a clean test/demo venv is created under /tmp.
    *_library_root_ignore_globs(),
)

def load_patterns(extra_file=None):
    """Load regex patterns from the shipped defaults and an optional extra file.

    Returns a list of compiled ``re.Pattern`` objects.
    """
    lines = []

    raw = pkgutil.get_data("retracesoftware", "retrace_patterns.txt")
    if raw:
        lines.extend(raw.decode("utf-8").splitlines())

    if extra_file:
        with open(extra_file, "r", encoding="utf-8") as f:
            lines.extend(f.read().splitlines())

    patterns = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(re.compile(line))

    return patterns


def _path_string(arg):
    try:
        return os.fsdecode(arg).replace("\\", "/")
    except TypeError:
        return str(arg).replace("\\", "/")


def _matches_ignore_glob(path, ignore_globs):
    return any(fnmatch.fnmatch(path, pattern) for pattern in ignore_globs)


def make_pathpredicate(patterns, verbose=False, ignore_globs=DEFAULT_IGNORE_GLOBS, fd_provenance=None):
    """Build a predicate callable from compiled regex patterns.

    The returned callable accepts a single argument (the value extracted
    from the intercepted call by ``functional.param``) and returns
    ``True`` (retrace) or ``False`` (passthrough).

    Parameters
    ----------
    patterns : list of re.Pattern
        Compiled regex patterns. Any match → retrace.
    verbose : bool
        If True, log each predicate evaluation (path and result) to stderr.
    """

    def predicate(arg):
        if isinstance(arg, int):
            if fd_provenance is not None and not fd_provenance.should_retrace_fd(arg):
                if verbose:
                    print(f"retrace pathpredicate: fd={arg} -> passthrough (fd came from passthrough path I/O)", file=sys.stderr)
                return False
            if verbose:
                print(f"retrace pathpredicate: fd={arg} -> retrace (fd always retraced)", file=sys.stderr)
            return True

        path = _path_string(arg)
        if _matches_ignore_glob(path, ignore_globs):
            if verbose:
                print(f"retrace pathpredicate: {path!r} ignored by glob -> passthrough", file=sys.stderr)
            return False

        for pat in patterns:
            if pat.search(path):
                if verbose:
                    print(f"retrace pathpredicate: {path!r} matched {pat.pattern!r} -> retrace", file=sys.stderr)
                return True

        if verbose:
            print(f"retrace pathpredicate: {path!r} no match -> passthrough", file=sys.stderr)
        return False

    return predicate
