import os

from retracesoftware.exceptions import (
    RetraceError,
    RecordError,
    ReplayError,
    ConfigurationError,
    VersionMismatchError,
    RecordingNotFoundError,
)

# Meson editable installs can expose a synthetic package path that does not
# automatically discover brand-new subpackages until the extension is rebuilt.
# Keep the live source directory on __path__ so incremental package additions
# like retracesoftware.protocol import cleanly during development and tests.
_package_dir = os.path.dirname(__file__)
if _package_dir not in __path__:
    __path__.append(_package_dir)

__all__ = [
    'RetraceError',
    'RecordError',
    'ReplayError',
    'ConfigurationError',
    'VersionMismatchError',
    'RecordingNotFoundError',
]
