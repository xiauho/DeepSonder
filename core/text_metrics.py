"""Shared text measurements for chapter prose and AI output."""

from __future__ import annotations

import re


def count_content_chars(text: str) -> int:
    """Count visible prose characters using the AI protocol's convention."""
    return len(re.sub(r"\s+", "", str(text or "")))
