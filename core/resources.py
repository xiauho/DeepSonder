"""Resolve application resources from the project directory."""

from __future__ import annotations

import sys
from pathlib import Path


def resource_root() -> Path:
    """Return the source root or PyInstaller's bundled data directory."""
    bundled_root = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and bundled_root:
        return Path(bundled_root).resolve()
    return Path(__file__).resolve().parent.parent


def resource_path(relative_path: str) -> Path:
    """Return an application resource in source and packaged executions."""
    return resource_root() / relative_path
