"""Manual end-to-end reproducer for flight-search auto-enable replay.

This scenario intentionally exercises the public ``.pth`` workflow:

    RETRACE_RECORDING=flight-relative-autoenable.retrace python test.py

It is not meant to be recorded with ``python -m retracesoftware --recording``;
the bug only reproduced with the auto-enable path and a relative recording
name.
"""

from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys


QUERY = (
    "Book the cheapest flight from Barcelona to a US city on the East Coast "
    "that is not NYC on 2026-02-14"
)


def main() -> None:
    app_dir_text = os.environ.get("FLIGHT_SEARCH_ASSISTANT_DIR")
    if not app_dir_text:
        raise RuntimeError(
            "set FLIGHT_SEARCH_ASSISTANT_DIR to the cookbook "
            "flight-search-assistant checkout"
        )

    app_dir = Path(app_dir_text).expanduser().resolve()
    script = app_dir / "flight_search.py"
    if not script.exists():
        raise RuntimeError(f"flight_search.py not found in {app_dir}")

    os.chdir(app_dir)
    sys.path.insert(0, str(app_dir))
    sys.argv = [
        "flight_search.py",
        "--query",
        QUERY,
    ]
    runpy.run_path(str(script), run_name="__main__")


if __name__ == "__main__":
    main()
