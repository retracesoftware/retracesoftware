"""Module configuration resolver for retrace.

Provides on-demand lookup of module patching configurations from:
  1. User directory (RETRACE_MODULES_PATH, default: .retrace/modules/)
  2. Built-in package directory (retracesoftware/modules/*.toml)

Two TOML file formats are supported:

  Grouped files (e.g., stdlib.toml):
      Section headers are Python module names.

      [posix]
      proxy = ["read", "write"]

      [_socket]
      proxy = ["socket", "getaddrinfo"]

  Single-module files (e.g., grpc._cython.cygrpc.toml):
      Filename is the module name. Root table holds base config.
      Optional 'package' key for version lookup.
      Optional version sections for version-specific additions.

      package = "grpcio"
      proxy = ["Channel", "Server"]

      [1.60]
      proxy = ["AioChannel"]
"""

import os
import tomllib
from pathlib import Path
from importlib.resources import files as resource_files

DIRECTIVE_KEYS = frozenset({
    "proxy", "immutable", "bind", "disable", "patch_hash",
    "wrap", "patch_class", "type_attributes", "patch_types",
    "default", "ignore",
})

DEFAULT_USER_MODULES_DIR = ".retrace/modules"


class ModuleConfigResolver:
    """Dict-like resolver for module patching configurations.

    Supports ``name in resolver``, ``resolver[name]``, and ``resolver.keys()``.
    User configs (RETRACE_MODULES_PATH) take precedence over built-in configs.
    """

    def __init__(self):
        self._configs = {}

        # User dir first — takes precedence (first registration wins)
        user_dir = Path(os.environ.get("RETRACE_MODULES_PATH", DEFAULT_USER_MODULES_DIR))
        if user_dir.is_dir():
            self._scan_filesystem_dir(user_dir)

        # Built-in package dir
        self._scan_builtin_dir()

    def _register(self, name, config):
        """Register a module config. First registration wins."""
        if name not in self._configs:
            self._configs[name] = config

    def _scan_filesystem_dir(self, directory):
        """Scan a filesystem directory for .toml config files."""
        for filepath in sorted(directory.glob("*.toml")):
            raw = tomllib.loads(filepath.read_text(encoding="utf-8"))
            self._process_file(filepath.stem, raw)

    def _scan_builtin_dir(self):
        """Scan the built-in retracesoftware.modules package for .toml files."""
        package = resource_files("retracesoftware.modules")
        for item in sorted(package.iterdir(), key=lambda x: x.name):
            if item.name.endswith(".toml"):
                raw = tomllib.loads(item.read_text(encoding="utf-8"))
                self._process_file(item.name[:-5], raw)

    def _process_file(self, stem, raw):
        """Process a parsed TOML file and register its module configs."""
        if self._is_single_module(raw):
            config = self._resolve_versioned(raw)
            if config is not None:
                self._register(stem, config)
        else:
            # Grouped file — each top-level key is a module name
            for name, config in raw.items():
                if isinstance(config, dict):
                    self._register(name, config)

    @staticmethod
    def _is_single_module(raw):
        """Determine if a parsed TOML file is a single-module file.

        Single-module files have non-dict values at the root level
        (directive lists, package string, etc.). Grouped files have
        only dict values (each dict is a module's config).
        """
        for value in raw.values():
            if not isinstance(value, dict):
                return True
        return False

    @staticmethod
    def _parse_version(ver_str):
        """Parse a version string into a comparable tuple of ints."""
        parts = []
        for part in str(ver_str).split("."):
            try:
                parts.append(int(part))
            except ValueError:
                break
        return tuple(parts)

    def _resolve_versioned(self, raw):
        """Resolve version sections for a single-module file.

        Returns the merged config dict, or None if the required package
        is not installed.
        """
        package_name = raw.pop("package", None)

        installed = None
        if package_name:
            try:
                import importlib.metadata
                installed = self._parse_version(
                    importlib.metadata.version(package_name)
                )
            except Exception:
                return None  # Package not installed — skip this file

        # Separate base config from version sections
        base = {}
        versions = {}
        for key, value in raw.items():
            if isinstance(value, dict) and key not in DIRECTIVE_KEYS:
                # Dict-valued key that isn't a known directive → version section
                versions[key] = value
            else:
                base[key] = value

        # Apply matching version sections in ascending order (additive)
        if installed is not None and versions:
            for ver_str in sorted(versions, key=self._parse_version):
                if installed >= self._parse_version(ver_str):
                    for directive, names in versions[ver_str].items():
                        if directive in base and isinstance(base[directive], list) and isinstance(names, list):
                            base[directive] = base[directive] + names
                        else:
                            base.setdefault(directive, names)

        return base

    def __contains__(self, name):
        return name in self._configs

    def keys(self):
        return self._configs.keys()

    def __getitem__(self, name):
        return self._configs[name]
