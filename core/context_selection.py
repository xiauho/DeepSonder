"""Deterministic, local-only ranking for task-related canon documents."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable


CONTEXT_SELECTION_MODES = {"safe", "legacy_all"}
WORLD_SELECTION_CHARS = 6_000
POWER_SELECTION_CHARS = 5_000
WORLD_ENTRY_CHARS = 1_600
POWER_ENTRY_CHARS = 1_400
SELECTION_TRIM_MARK = "\n…（资料条目过长，已为相关性筛选截断）"


@dataclass(frozen=True)
class CanonSelectionStat:
    """Redacted selection counts for one canon category."""

    category: str
    mode: str
    candidates: int
    included: int
    matched: int
    required: int = 0
    excluded_unmatched: int = 0
    excluded_capacity: int = 0
    reasons: tuple[tuple[str, int], ...] = ()
    prompt_included: int | None = None


@dataclass(frozen=True)
class RankedCanon:
    text: str
    stat: CanonSelectionStat


@dataclass(frozen=True)
class _Candidate:
    path: Path
    text: str
    score: int
    reason: str


def normalize_selection_mode(value: object) -> str:
    mode = str(value or "safe").strip().lower()
    return mode if mode in CONTEXT_SELECTION_MODES else "safe"


def select_ranked_documents(
    paths: Iterable[Path],
    *,
    query: str,
    category: str,
    mode: str,
    reader: Callable[[Path], str],
    total_chars: int,
    entry_chars: int,
    add_heading: bool = True,
) -> RankedCanon:
    """Rank and bound documents without networking or model inference."""
    mode = normalize_selection_mode(mode)
    candidates: list[_Candidate] = []
    for path in sorted({Path(item) for item in paths}, key=lambda item: item.name.casefold()):
        text = str(reader(path) or "").strip()
        if not text:
            continue
        score, reason = relevance_score(path, text, query)
        candidates.append(_Candidate(path, text, score, reason))

    if mode == "legacy_all":
        rendered = [_render_candidate(item, None, add_heading) for item in candidates]
        return RankedCanon(
            "\n\n".join(rendered),
            _selection_stat(category, mode, candidates, len(candidates), (), ()),
        )

    ordered = sorted(
        candidates,
        key=lambda item: (-item.score, item.path.name.casefold()),
    )
    remaining = max(0, int(total_chars))
    rendered: list[str] = []
    included: list[_Candidate] = []
    excluded_unmatched: list[_Candidate] = []
    excluded_capacity: list[_Candidate] = []
    for item in ordered:
        if remaining <= 0:
            target = excluded_capacity if item.score > 0 else excluded_unmatched
            target.append(item)
            continue
        piece = _render_candidate(item, max(160, int(entry_chars)), add_heading)
        if len(piece) > remaining:
            if remaining < 160:
                target = excluded_capacity if item.score > 0 else excluded_unmatched
                target.append(item)
                remaining = 0
                continue
            piece = _trim(piece, remaining)
        rendered.append(piece)
        included.append(item)
        remaining -= len(piece)

    return RankedCanon(
        "\n\n".join(rendered),
        _selection_stat(
            category,
            mode,
            candidates,
            len(included),
            excluded_unmatched,
            excluded_capacity,
        ),
    )


def relevance_score(path: Path, text: str, query: str) -> tuple[int, str]:
    """Score explicit, explainable references; avoid fuzzy semantic guesses."""
    haystack = _normalize(query)
    if not haystack:
        return 0, "background"
    title = _normalize(path.stem.replace("_", " ").replace("-", " "))
    if len(title) >= 2 and title in haystack:
        return 100, "title_match"

    headings = re.findall(r"^#{1,4}\s+(.+?)\s*$", text, flags=re.MULTILINE)
    for heading in headings:
        candidate = _normalize(re.sub(r"[*_`]+", "", heading))
        if len(candidate) >= 2 and candidate in haystack:
            return 70, "heading_match"

    for line in text.splitlines():
        match = re.match(r"\s*(?:别名|标签|关键词|关联地点|相关角色)\s*[：:]\s*(.+)", line)
        if not match:
            continue
        for token in re.split(r"[,，、;/；|\s]+", match.group(1)):
            candidate = _normalize(token)
            if len(candidate) >= 2 and candidate in haystack:
                return 60, "metadata_match"
    return 0, "background"


def required_stat(category: str, count: int, reason: str, mode: str) -> CanonSelectionStat:
    count = max(0, int(count))
    reasons = ((reason, count),) if count else ()
    return CanonSelectionStat(
        category=category,
        mode=normalize_selection_mode(mode),
        candidates=count,
        included=count,
        matched=count,
        required=count,
        reasons=reasons,
    )


def _selection_stat(
    category: str,
    mode: str,
    candidates: list[_Candidate],
    included: int,
    excluded_unmatched: Iterable[_Candidate],
    excluded_capacity: Iterable[_Candidate],
) -> CanonSelectionStat:
    reasons: dict[str, int] = {}
    for item in candidates:
        reasons[item.reason] = reasons.get(item.reason, 0) + 1
    return CanonSelectionStat(
        category=category,
        mode=mode,
        candidates=len(candidates),
        included=included,
        matched=sum(1 for item in candidates if item.score > 0),
        excluded_unmatched=len(tuple(excluded_unmatched)),
        excluded_capacity=len(tuple(excluded_capacity)),
        reasons=tuple(sorted(reasons.items())),
    )


def _render_candidate(item: _Candidate, cap: int | None, add_heading: bool) -> str:
    body = item.text
    prefix = f"### {item.path.stem}\n" if add_heading else ""
    rendered = prefix + body
    return _trim(rendered, cap) if cap is not None and len(rendered) > cap else rendered


def _trim(text: str, cap: int) -> str:
    cap = max(len(SELECTION_TRIM_MARK) + 1, int(cap))
    return text[: cap - len(SELECTION_TRIM_MARK)].rstrip() + SELECTION_TRIM_MARK


def _normalize(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "").casefold())
