"""Deterministic paragraph-aware chapter chunking for AI map tasks."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from .token_budget import (
    DEFAULT_CHUNK_OVERLAP_TOKENS,
    DEFAULT_CHUNK_TOKEN_BUDGET,
    DEFAULT_TOKEN_ESTIMATOR,
    ConservativeTokenEstimator,
)


@dataclass(frozen=True)
class ChapterChunk:
    chunk_id: str
    chapter_id: str
    index: int
    start_paragraph: int
    end_paragraph: int
    text: str
    annotated_text: str
    estimated_tokens: int
    content_hash: str

    @property
    def anchor(self) -> str:
        return (
            f"{self.chapter_id}:p{self.start_paragraph:04d}"
            f"-p{self.end_paragraph:04d}"
        )


@dataclass(frozen=True)
class _Unit:
    paragraph: int
    text: str


def chapter_content_hash(text: object) -> str:
    normalized = _normalize(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def chunk_chapter(
    chapter_id: str,
    text: object,
    *,
    target_tokens: int = DEFAULT_CHUNK_TOKEN_BUDGET,
    overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
    estimator: ConservativeTokenEstimator = DEFAULT_TOKEN_ESTIMATOR,
) -> tuple[ChapterChunk, ...]:
    """Split a chapter at paragraph/sentence boundaries with bounded overlap."""
    chapter_id = str(chapter_id or "").strip()
    if not chapter_id:
        raise ValueError("章节分块缺少 chapter_id。")
    target = max(256, int(target_tokens))
    overlap = max(0, min(int(overlap_tokens), target // 3))
    paragraphs = _paragraphs(text)
    if not paragraphs:
        return ()

    units: list[_Unit] = []
    anchor_allowance = estimator.estimate(f"[{chapter_id}:p0000]\n")
    unit_target = max(128, target - anchor_allowance)
    for paragraph_number, paragraph in enumerate(paragraphs, 1):
        pieces = _split_to_budget(paragraph, unit_target, estimator)
        units.extend(_Unit(paragraph_number, piece) for piece in pieces)

    groups: list[list[_Unit]] = []
    current: list[_Unit] = []
    for unit in units:
        candidate = [*current, unit]
        if current and _estimate_units(candidate, chapter_id, estimator) > target:
            groups.append(current)
            current = _overlap_tail(current, overlap, chapter_id, estimator)
            while current and _estimate_units(
                [*current, unit], chapter_id, estimator
            ) > target:
                current.pop(0)
        current.append(unit)
    if current:
        groups.append(current)

    chunks: list[ChapterChunk] = []
    for index, group in enumerate(groups, 1):
        rendered = "\n\n".join(unit.text.strip() for unit in group if unit.text.strip())
        annotated = _render_annotated(group, chapter_id)
        estimated = estimator.estimate(annotated)
        if estimated > target:
            raise RuntimeError("章节分块超过配置的 token 上限。")
        start = min(unit.paragraph for unit in group)
        end = max(unit.paragraph for unit in group)
        chunk_id = f"{chapter_id}:c{index:04d}:p{start:04d}-p{end:04d}"
        chunks.append(
            ChapterChunk(
                chunk_id=chunk_id,
                chapter_id=chapter_id,
                index=index,
                start_paragraph=start,
                end_paragraph=end,
                text=rendered,
                annotated_text=annotated,
                estimated_tokens=estimated,
                content_hash=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
            )
        )
    return tuple(chunks)


def _paragraphs(text: object) -> list[str]:
    normalized = _normalize(text).strip()
    if not normalized:
        return []
    return [
        block.strip()
        for block in re.split(r"\n[ \t]*\n+", normalized)
        if block.strip()
    ]


def _normalize(text: object) -> str:
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _split_to_budget(
    text: str,
    target: int,
    estimator: ConservativeTokenEstimator,
) -> list[str]:
    if estimator.estimate(text) <= target:
        return [text]
    pieces: list[str] = []
    start = 0
    while start < len(text):
        low = start + 1
        high = len(text)
        best = low
        while low <= high:
            middle = (low + high) // 2
            if estimator.estimate(text[start:middle]) <= target:
                best = middle
                low = middle + 1
            else:
                high = middle - 1
        end = best
        if end < len(text):
            floor = start + max(1, int((end - start) * 0.65))
            boundary = _preferred_boundary(text, floor, end)
            if boundary is not None:
                end = boundary
        piece = text[start:end].strip()
        if not piece:
            end = max(start + 1, best)
            piece = text[start:end]
        pieces.append(piece)
        start = end
        while start < len(text) and text[start].isspace():
            start += 1
    return pieces


def _preferred_boundary(text: str, floor: int, end: int) -> int | None:
    for index in range(end - 1, floor - 1, -1):
        if text[index] in "。！？!?；;\n ":
            return index + 1
    return None


def _estimate_units(
    units: list[_Unit],
    chapter_id: str,
    estimator: ConservativeTokenEstimator,
) -> int:
    return estimator.estimate(_render_annotated(units, chapter_id))


def _overlap_tail(
    units: list[_Unit],
    overlap_tokens: int,
    chapter_id: str,
    estimator: ConservativeTokenEstimator,
) -> list[_Unit]:
    if overlap_tokens <= 0:
        return []
    selected: list[_Unit] = []
    for unit in reversed(units):
        candidate = [unit, *selected]
        if _estimate_units(candidate, chapter_id, estimator) > overlap_tokens:
            break
        selected = candidate
    return selected


def _render_annotated(units: list[_Unit], chapter_id: str) -> str:
    return "\n\n".join(
        f"[{chapter_id}:p{unit.paragraph:04d}]\n{unit.text.strip()}"
        for unit in units
        if unit.text.strip()
    )
