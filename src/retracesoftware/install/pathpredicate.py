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

DEFAULT_IGNORE_GLOBS = (
    "__pycache__",
    "__pycache__/*",
    "*/__pycache__",
    "*/__pycache__/*",
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


def make_pathpredicate(patterns, verbose=False, ignore_globs=DEFAULT_IGNORE_GLOBS):
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
