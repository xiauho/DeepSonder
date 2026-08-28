"""Exact, explainable text anchors used by consistency reports.

The model only returns the quoted sentence.  The application resolves that
quote against the current editor text and never trusts model supplied line or
character offsets.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TextAnchor:
    quote: str
    start: int
    end: int
    prefix: str = ""
    suffix: str = ""
    line: int = 1
    text_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "quote": self.quote,
            "start": self.start,
            "end": self.end,
            "prefix": self.prefix,
            "suffix": self.suffix,
            "line": self.line,
            "text_hash": self.text_hash,
        }


@dataclass(frozen=True)
class AnchorResolution:
    status: str
    start: int | None = None
    end: int | None = None
    occurrences: int = 0


def build_text_anchor(text: str, quote: str, *, context_chars: int = 48) -> dict[str, Any] | None:
    """Create an anchor only when the quote occurs exactly once."""
    text = str(text or "")
    quote = str(quote or "").strip()
    if not quote:
        return None
    body_start, body_text = _chapter_body_slice(text)
    body_matches = _exact_matches(body_text, quote)
    matches = (
        [(body_start + start, body_start + end) for start, end in body_matches]
        if body_matches
        else _exact_matches(text, quote)
    )
    if len(matches) != 1:
        return None
    start, end = matches[0]
    anchor = TextAnchor(
        quote=quote,
        start=start,
        end=end,
        prefix=text[max(0, start - context_chars) : start],
        suffix=text[end : min(len(text), end + context_chars)],
        line=text.count("\n", 0, start) + 1,
        text_hash=_hash_text(text),
    )
    return anchor.as_dict()


def resolve_text_anchor(text: str, anchor: dict[str, Any] | str | None) -> AnchorResolution:
    """Resolve an anchor after edits, preferring exact and then whitespace matches."""
    text = str(text or "")
    if isinstance(anchor, str):
        quote = anchor.strip()
        expected_start = None
    elif isinstance(anchor, dict):
        quote = str(anchor.get("quote") or "").strip()
        raw_start = anchor.get("start")
        try:
            expected_start = int(raw_start) if raw_start is not None else None
        except (TypeError, ValueError):
            expected_start = None
    else:
        return AnchorResolution("missing")
    if not quote:
        return AnchorResolution("missing")

    exact = _exact_matches(text, quote)
    if len(exact) == 1:
        start, end = exact[0]
        return AnchorResolution(
            "exact" if expected_start is None or expected_start == start else "relocated",
            start,
            end,
            1,
        )
    if len(exact) > 1:
        selected = _nearest_unique(exact, expected_start)
        if selected is not None:
            return AnchorResolution("relocated", selected[0], selected[1], len(exact))
        return AnchorResolution("ambiguous", occurrences=len(exact))

    whitespace = _whitespace_matches(text, quote)
    if len(whitespace) == 1:
        start, end = whitespace[0]
        return AnchorResolution("relocated", start, end, 1)
    if len(whitespace) > 1:
        selected = _nearest_unique(whitespace, expected_start)
        if selected is not None:
            return AnchorResolution("relocated", selected[0], selected[1], len(whitespace))
        return AnchorResolution("ambiguous", occurrences=len(whitespace))
    return AnchorResolution("missing")


def enrich_report_anchors(report: dict[str, Any], text: str) -> dict[str, Any]:
    """Attach generated anchors to a validated report without mutating it."""
    enriched = dict(report)
    issues = []
    for issue in report.get("issues", []):
        item = dict(issue)
        quote = str(item.get("chapter_quote") or "").strip()
        if quote:
            anchor = build_text_anchor(text, quote)
            item["chapter_anchor"] = anchor or {"quote": quote, "status": "unresolved"}
        else:
            item["chapter_anchor"] = {"status": "missing"}
        issues.append(item)
    enriched["issues"] = issues
    return enriched


def render_anchor_context(text: str, quote: str, *, context_chars: int = 700) -> str:
    """Render a bounded chapter window around a quote for repair prompts."""
    text = str(text or "")
    quote = str(quote or "").strip()
    if not quote:
        return text[: context_chars * 2]
    resolution = resolve_text_anchor(text, quote)
    if resolution.start is None or resolution.end is None:
        return text[: context_chars * 2]
    start = max(0, resolution.start - context_chars)
    end = min(len(text), resolution.end + context_chars)
    return text[start:end]


def _exact_matches(text: str, quote: str) -> list[tuple[int, int]]:
    matches: list[tuple[int, int]] = []
    start = 0
    while True:
        index = text.find(quote, start)
        if index < 0:
            return matches
        matches.append((index, index + len(quote)))
        start = index + max(1, len(quote))


def _chapter_body_slice(text: str) -> tuple[int, str]:
    """Return the Markdown ``## 正文`` section when the chapter has one."""
    match = re.search(r"(?im)^##\s+正文\s*$", text)
    if match is None:
        return 0, text
    start = match.end()
    next_heading = re.search(r"(?im)^##\s+", text[start:])
    end = start + next_heading.start() if next_heading else len(text)
    return start, text[start:end]


def _whitespace_matches(text: str, quote: str) -> list[tuple[int, int]]:
    parts = [part for part in re.split(r"\s+", quote.strip()) if part]
    if not parts:
        return []
    pattern = r"\s*".join(re.escape(part) for part in parts)
    return [(match.start(), match.end()) for match in re.finditer(pattern, text)]


def _nearest_unique(
    matches: list[tuple[int, int]], expected_start: int | None
) -> tuple[int, int] | None:
    if expected_start is None:
        return None
    distances = sorted((abs(start - expected_start), start, end) for start, end in matches)
    if len(distances) > 1 and distances[0][0] == distances[1][0]:
        return None
    return distances[0][1], distances[0][2]


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
