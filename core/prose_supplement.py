"""Safe insertion-only supplementation shared by expansion and continuation."""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from . import ai_protocol
from .dsh_client import DSHClient
from .prompt_builder import build_prose_supplement_prompt
from .task_controller import AITaskCancelled
from .text_metrics import count_content_chars


@dataclass(frozen=True)
class ProseSupplementRunResult:
    text: str
    initial_char_count: int
    final_char_count: int
    added_char_count: int
    applied: bool
    warning: str = ""


@dataclass(frozen=True)
class ProseInsertionPoint:
    anchor_id: str
    offset: int
    preview: str


def run_prose_supplement(
    chapter_id: str,
    prose: str,
    target_chars: int,
    dsh: DSHClient,
    *,
    cancel_event: threading.Event | None = None,
    task_kind: str = "prose_length_supplement",
    story_constraints: str = "",
) -> ProseSupplementRunResult:
    """Request and safely apply one bounded insertion-only supplement."""
    source = str(prose or "").strip()
    initial = count_content_chars(source)
    target = max(initial + 1, int(target_chars))
    points = build_prose_insertion_points(source)
    point_offsets = {point.anchor_id: point.offset for point in points}
    prompt = build_prose_supplement_prompt(
        chapter_id,
        source,
        target,
        task_kind=task_kind,
        insertion_points=tuple(
            {
                "anchor_id": point.anchor_id,
                "preview": point.preview,
            }
            for point in points
        ),
        story_constraints=story_constraints,
    )
    options = {"cancel_event": cancel_event} if cancel_event is not None else {}
    missing = max(1, target - initial)
    try:
        raw = dsh.generate_json(
            prompt.system_prompt,
            prompt.user_prompt,
            context_report=prompt.report,
            **options,
        )
        supplement = ai_protocol.parse_prose_supplement(
            raw,
            expected_chapter_id=chapter_id,
            source_text=source,
            max_added_chars=max(300, round(missing * 1.75)),
            insertion_points=point_offsets,
        )
        merged = apply_prose_insertions(
            source,
            supplement.insertions,
            insertion_points=point_offsets,
        )
        final = count_content_chars(merged)
        added = max(0, final - initial)
        warning = ""
        if supplement.warnings:
            warning = "部分差额补写未应用：" + "；".join(supplement.warnings)
        return ProseSupplementRunResult(
            text=merged,
            initial_char_count=initial,
            final_char_count=final,
            added_char_count=added,
            applied=added > 0,
            warning=warning,
        )
    except AITaskCancelled:
        raise
    except Exception as exc:  # noqa: BLE001 - preserve the usable first draft
        return ProseSupplementRunResult(
            text=source,
            initial_char_count=initial,
            final_char_count=initial,
            added_char_count=0,
            applied=False,
            warning=f"差额补写未能安全应用：{exc}",
        )


def apply_prose_insertions(
    source: str,
    insertions: tuple[ai_protocol.ExpansionInsertion, ...],
    *,
    insertion_points: dict[str, int] | None = None,
) -> str:
    """Apply validated insertions by descending source offset."""
    operations: list[tuple[int, str]] = []
    used_offsets: set[int] = set()
    for insertion in insertions:
        if insertion.anchor_id:
            offsets = insertion_points or {}
            if insertion.anchor_id not in offsets:
                raise ai_protocol.AIProtocolError("差额补写包含未知的插入位置编号。")
            offset = int(offsets[insertion.anchor_id])
        else:
            start = source.index(insertion.anchor)
            offset = (
                start
                if insertion.position == "before"
                else start + len(insertion.anchor)
            )
        if offset in used_offsets:
            raise ai_protocol.AIProtocolError("差额补写包含冲突的插入位置。")
        used_offsets.add(offset)
        addition = (
            insertion.text.rstrip() + "\n\n"
            if insertion.position == "before"
            else "\n\n" + insertion.text.lstrip()
        )
        operations.append((offset, addition))
    result = source
    for offset, addition in sorted(operations, reverse=True):
        result = result[:offset] + addition + result[offset:]
    return result


def build_prose_insertion_points(
    source: str,
    *,
    maximum: int = 12,
) -> tuple[ProseInsertionPoint, ...]:
    """Create stable paragraph-boundary IDs without asking the model to quote text."""
    text = str(source or "")
    spans: list[tuple[int, str]] = []
    for match in re.finditer(r"\S(?:[\s\S]*?\S)?(?=(?:\r?\n){2,}|\Z)", text):
        paragraph = match.group(0).rstrip()
        if paragraph:
            spans.append((match.start() + len(paragraph), paragraph))
    if not spans and text:
        spans.append((len(text), text))
    limit = max(1, int(maximum))
    if len(spans) > limit:
        indexes = {
            round(index * (len(spans) - 1) / (limit - 1))
            for index in range(limit)
        } if limit > 1 else {len(spans) - 1}
        spans = [spans[index] for index in sorted(indexes)]
    return tuple(
        ProseInsertionPoint(
            anchor_id=f"P{index:03d}",
            offset=offset,
            preview="段落末尾：“" + re.sub(r"\s+", "", paragraph)[-56:] + "”之后",
        )
        for index, (offset, paragraph) in enumerate(spans, start=1)
    )
