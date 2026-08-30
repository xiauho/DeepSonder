"""Platform-appropriate locations for Novalist application data."""

from __future__ import annotations

import os
import sys
from pathlib import Path


APP_DIRECTORY_NAME = "Novalist"


def app_config_dir() -> Path:
    """Return the per-user directory for durable application settings."""
    if sys.platform == "win32":
        base = os.environ.get("APPDATA")
        return (
            Path(base) / APP_DIRECTORY_NAME
            if base
            else _home() / "AppData" / "Roaming" / APP_DIRECTORY_NAME
        )
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / APP_DIRECTORY_NAME
    base = os.environ.get("XDG_CONFIG_HOME")
    return (
        Path(base) / APP_DIRECTORY_NAME
        if base
        else _home() / ".config" / APP_DIRECTORY_NAME
    )


def app_cache_dir() -> Path:
    """Return the per-user directory for disposable application data."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return (
            Path(base) / APP_DIRECTORY_NAME
            if base
            else _home() / "AppData" / "Local" / APP_DIRECTORY_NAME
        )
    if sys.platform == "darwin":
        return _home() / "Library" / "Caches" / APP_DIRECTORY_NAME
    base = os.environ.get("XDG_CACHE_HOME")
    return (
        Path(base) / APP_DIRECTORY_NAME
        if base
        else _home() / ".cache" / APP_DIRECTORY_NAME
    )


def update_cache_dir() -> Path:
    """Return the directory reserved for downloaded update artifacts."""
    return app_cache_dir() / "updates"


def legacy_application_root() -> Path:
    """Return the old writable application root used before per-user storage."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _home() -> Path:
    return Path.home()
