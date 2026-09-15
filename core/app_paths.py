"""Platform-appropriate locations for DeepSonder-Electron application data."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIRECTORY_PARTS = ("DeepSonder", "Electron")


def _product_directory(base: Path) -> Path:
    return base.joinpath(*APP_DIRECTORY_PARTS)


def app_config_dir() -> Path:
    """Return the per-user directory for durable application settings."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (
            _product_directory(Path(base))
            if base
            else _product_directory(_home() / "AppData" / "Roaming")
        )
    if sys.platform == "darwin":
        return _product_directory(_home() / "Library" / "Application Support")
    base = os.environ.get("XDG_CONFIG_HOME")
    return (
        _product_directory(Path(base))
        if base
        else _product_directory(_home() / ".config")
    )


def app_cache_dir() -> Path:
    """Return the per-user directory for disposable application data."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return (
            _product_directory(Path(base))
            if base
            else _product_directory(_home() / "AppData" / "Local")
        )
    if sys.platform == "darwin":
        return _product_directory(_home() / "Library" / "Caches")
    base = os.environ.get("XDG_CACHE_HOME")
    return (
        _product_directory(Path(base))
        if base
        else _product_directory(_home() / ".cache")
    )


def update_cache_dir() -> Path:
    """Return the directory reserved for downloaded update artifacts."""
    return app_cache_dir() / "updates"


def _home() -> Path:
    return Path.home()
