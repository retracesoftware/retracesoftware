"""
Retrace exception hierarchy.

All retrace-specific exceptions inherit from RetraceError.
"""

class RetraceError(Exception):
    """Base exception for all retrace errors."""
    pass


class RecordError(RetraceError):
    """Error during recording phase."""
    pass


class ReplayError(RetraceError):
    """Error during replay phase."""
    pass


class ConfigurationError(RetraceError):
    """Error in retrace configuration."""
    pass


class VersionMismatchError(RetraceError):
    """Recording and replay versions don't match."""
    pass


class RecordingNotFoundError(RetraceError):
    """Recording path does not exist."""
    pass
