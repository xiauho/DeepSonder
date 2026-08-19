"""Resolve application resources from the project directory."""

from __future__ import annotations

from pathlib import Path


def resource_path(relative_path: str) -> Path:
    """Return an asset path relative to the project root."""
    return Path(__file__).resolve().parent.parent / relative_path
