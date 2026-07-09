"""PEP 517 backend wrapper: keep meson-python build artifacts under build/."""

from __future__ import annotations

from typing import Any

import mesonpy
from mesonpy._tags import get_abi_tag

__all__ = [
    "build_editable",
    "build_sdist",
    "build_wheel",
    "get_requires_for_build_editable",
    "get_requires_for_build_sdist",
    "get_requires_for_build_wheel",
]

get_requires_for_build_wheel = mesonpy.get_requires_for_build_wheel
get_requires_for_build_sdist = mesonpy.get_requires_for_build_sdist
get_requires_for_build_editable = mesonpy.get_requires_for_build_editable


def _with_build_dir(config_settings: dict[str, Any] | None) -> dict[str, Any]:
    settings = dict(config_settings or {})
    if "build-dir" not in settings and "builddir" not in settings:
        settings["build-dir"] = f"build/{get_abi_tag()}"
    return settings


def build_wheel(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    return mesonpy.build_wheel(
        wheel_directory,
        _with_build_dir(config_settings),
        metadata_directory,
    )


def build_sdist(
    sdist_directory: str,
    config_settings: dict[str, Any] | None = None,
) -> str:
    return mesonpy.build_sdist(sdist_directory, _with_build_dir(config_settings))


def build_editable(
    wheel_directory: str,
    config_settings: dict[str, Any] | None = None,
    metadata_directory: str | None = None,
) -> str:
    return mesonpy.build_editable(
        wheel_directory,
        _with_build_dir(config_settings),
        metadata_directory,
    )
