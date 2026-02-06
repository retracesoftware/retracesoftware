# Extend namespace to include retracesoftware.utils, retracesoftware.functional, etc.
__path__ = __import__('pkgutil').extend_path(__path__, __name__)

from retracesoftware.exceptions import (
    RetraceError,
    RecordError,
    ReplayError,
    ConfigurationError,
    VersionMismatchError,
    RecordingNotFoundError,
)

__all__ = [
    'RetraceError',
    'RecordError',
    'ReplayError',
    'ConfigurationError',
    'VersionMismatchError',
    'RecordingNotFoundError',
]
