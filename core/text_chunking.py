"""Deterministic paragraph-aware chapter chunking for AI map tasks."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, replace

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
    # Memory-only metadata. Positions never participate in a reusable identity.
    chunk_strategy: str = "positional-v1"
    evidence_hashes: tuple[str, ...] = ()
    context_hashes: tuple[str, ...] = ()
    context_before: str = ""
    context_after: str = ""
    reuse_scope: str = ""

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


def stable_memory_chunks(
    chapter_id: str,
    text: object,
    *,
    target_tokens: int = DEFAULT_CHUNK_TOKEN_BUDGET,
    overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
    estimator: ConservativeTokenEstimator = DEFAULT_TOKEN_ESTIMATOR,
) -> tuple[ChapterChunk, ...]:
    """Content-defined paragraph boundaries that can resynchronize after edits.

    A minimum size avoids tiny requests; a hard maximum protects the budget.
    A paragraph's content hash chooses the intervening cut, independently of
    its absolute index. Forced cuts can shift locally, then rejoin natural cuts.
    Oversized single paragraphs retain the established lossless splitter.
    """
    chapter_id = str(chapter_id or "").strip()
    if not chapter_id:
        raise ValueError("章节分块缺少 chapter_id。")
    paragraphs = _paragraphs(text)
    if len(paragraphs) <= 1:
        return bind_memory_chunk_context(chunk_chapter(chapter_id, text,
            target_tokens=target_tokens, overlap_tokens=overlap_tokens, estimator=estimator),
            text, estimator=estimator)
    target = max(256, int(target_tokens))
    overlap = max(0, min(int(overlap_tokens), target // 3))
    core_target = max(128, target - overlap)
    allowance = estimator.estimate(f"[{chapter_id}:p0000]\n")
    units = [
        _Unit(number, piece)
        for number, paragraph in enumerate(paragraphs, 1)
        for piece in _split_to_budget(paragraph, max(64, core_target - allowance), estimator)
    ]
    groups: list[list[_Unit]] = []
    current: list[_Unit] = []
    for unit in units:
        if current and _estimate_units([*current, unit], chapter_id, estimator) > core_target:
            groups.append(current)
            current = []
        current.append(unit)
        size = _estimate_units(current, chapter_id, estimator)
        score = int(hashlib.sha256(unit.text.encode("utf-8")).hexdigest()[:16], 16)
        probability = min(1.0, estimator.estimate(unit.text) / max(1, core_target / 2))
        if size >= core_target // 2 and score < int(probability * (1 << 64)):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    chunks = []
    previous: list[_Unit] = []
    for index, core in enumerate(groups, 1):
        tail = _overlap_tail(previous, overlap, chapter_id, estimator)
        while tail and _estimate_units([*tail, *core], chapter_id, estimator) > target:
            tail.pop(0)
        group = [*tail, *core]
        rendered = "\n\n".join(unit.text for unit in group)
        annotated = _render_annotated(group, chapter_id)
        estimated = estimator.estimate(annotated)
        if estimated > target:
            raise RuntimeError("章节分块超过配置的 token 上限。")
        start, end = group[0].paragraph, group[-1].paragraph
        chunks.append(ChapterChunk(
            chunk_id=f"{chapter_id}:c{index:04d}:p{start:04d}-p{end:04d}",
            chapter_id=chapter_id, index=index, start_paragraph=start, end_paragraph=end,
            text=rendered, annotated_text=annotated, estimated_tokens=estimated,
            content_hash=hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
        ))
        previous = core
    return bind_memory_chunk_context(tuple(chunks), text, estimator=estimator)


def bind_memory_chunk_context(
    chunks: tuple[ChapterChunk, ...], text: object, *,
    estimator: ConservativeTokenEstimator = DEFAULT_TOKEN_ESTIMATOR,
) -> tuple[ChapterChunk, ...]:
    """Bind exact paragraph evidence and immediate neighboring context.

    Repeated paragraphs and split-paragraph evidence remain snapshot/position
    bound. Context is bounded for transport, but its hash covers the WHOLE
    adjacent paragraph, so an edit outside the sent excerpt also invalidates.
    """
    paragraphs = _paragraphs(text)
    hashes = [hashlib.sha256(p.encode("utf-8")).hexdigest() for p in paragraphs]
    counts = Counter(hashes)
    full_hash = chapter_content_hash(text)
    excerpts: dict[int, list[str]] = {}

    def context(number: int, before: bool) -> str:
        if not 0 <= number < len(paragraphs):
            return ""
        if number not in excerpts:
            excerpts[number] = _split_to_budget(paragraphs[number], 256, estimator)
        return excerpts[number][-1 if before else 0]

    result = []
    for chunk in chunks:
        start, end = chunk.start_paragraph - 1, chunk.end_paragraph
        evidence = tuple(hashes[start:end])
        neighbors = (hashes[start - 1] if start else "BOF",
                     hashes[end] if end < len(hashes) else "EOF")
        ambiguous = any(counts[h] > 1 for h in (*evidence, *neighbors))
        # A partial paragraph cannot identify which occurrence of its evidence
        # was used. Do not move it across snapshots, even if a piece looks equal.
        covered = "\n\n".join(paragraphs[start:end])
        partial = chunk.text != covered
        scope = f"{full_hash}|{chunk.chunk_id}" if ambiguous or partial else ""
        result.append(replace(chunk, chunk_strategy="memory-cdc-v1",
            evidence_hashes=evidence, context_hashes=neighbors,
            context_before=context(start - 1, True), context_after=context(end, False),
            reuse_scope=scope))
    return tuple(result)


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
