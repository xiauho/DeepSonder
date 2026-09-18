"""Platform-appropriate locations for DeepSonder-PySide6 application data."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIRECTORY_PARTS = ("DeepSonder", "PySide6")


def _under(base: Path) -> Path:
    return base.joinpath(*APP_DIRECTORY_PARTS)


def app_config_dir() -> Path:
    """Return the per-user directory for durable application settings."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (
            _under(Path(base))
            if base
            else _under(_home() / "AppData" / "Roaming")
        )
    if sys.platform == "darwin":
        return _under(_home() / "Library" / "Application Support")
    base = os.environ.get("XDG_CONFIG_HOME")
    return (
        _under(Path(base))
        if base
        else _under(_home() / ".config")
    )


def app_cache_dir() -> Path:
    """Return the per-user directory for disposable application data."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return (
            _under(Path(base))
            if base
            else _under(_home() / "AppData" / "Local")
        )
    if sys.platform == "darwin":
        return _under(_home() / "Library" / "Caches")
    base = os.environ.get("XDG_CACHE_HOME")
    return (
        _under(Path(base))
        if base
        else _under(_home() / ".cache")
    )


def _home() -> Path:
    return Path.home()
