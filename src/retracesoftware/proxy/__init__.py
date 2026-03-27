"""Proxy package.

Editable installs load this package through a custom loader that can leave
``__path__`` pointing only at the loader shim. Append the source directory so
new helper modules added alongside existing files remain importable without
rebuilding the editable install.
"""

from pathlib import Path


_HERE = str(Path(__file__).resolve().parent)
if _HERE not in __path__:
    __path__.append(_HERE)

