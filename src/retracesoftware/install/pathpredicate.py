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

from retracesoftware.functional import or_predicate, sequence, isinstanceof

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


def make_pathpredicate(patterns, verbose=False):
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

    if verbose:
        def int_predicate(arg):
            if isinstance(arg, int):
                print(f"retrace pathpredicate: fd={arg} → retrace (fd always retraced)", file=sys.stderr)
                return True
            return False

        def pattern_predicate(s):
            for pat in patterns:
                if pat.search(s):
                    print(f"retrace pathpredicate: {s!r} matched {pat.pattern!r} → retrace", file=sys.stderr)
                    return True
            print(f"retrace pathpredicate: {s!r} no match → passthrough", file=sys.stderr)
            return False

    else:
        int_predicate = isinstanceof(int)

        matchers = [pat.search for pat in patterns]

        pattern_predicate = or_predicate(*matchers)

    return or_predicate(int_predicate, sequence(str, pattern_predicate))
