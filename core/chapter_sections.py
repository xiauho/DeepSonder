"""Pure helpers for locating editable chapter sections in Markdown."""

from __future__ import annotations

import re


_BODY_HEADING = re.compile(r"(?im)^[ \t]*##[ \t]+正文[ \t]*$")
_SECTION_HEADING = re.compile(r"(?im)^[ \t]*##[ \t]+.+?$")


def chapter_body_bounds(raw: str) -> tuple[int, int] | None:
    """Return the正文 content bounds, excluding trailing section whitespace."""
    text = str(raw or "")
    body = _BODY_HEADING.search(text)
    if body is None:
        return None
    start = body.end()
    next_heading = _SECTION_HEADING.search(text, start)
    section_end = next_heading.start() if next_heading else len(text)
    content_end = start + len(text[start:section_end].rstrip())
    return start, content_end


def chapter_body_text(raw: str) -> str:
    """Return the正文 section text for live editor measurements."""
    text = str(raw or "")
    bounds = chapter_body_bounds(text)
    if bounds is None:
        # Unsectioned imported chapters are treated as正文 by the project parser.
        return re.sub(r"(?m)^#\s+.*$", "", text, count=1).strip()
    start, end = bounds
    return text[start:end].strip()
