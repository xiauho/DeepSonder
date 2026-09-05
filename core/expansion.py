"""Chapter expansion orchestration."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, replace

from . import ai_protocol
from .context_budget import EXPANSION_SUMMARY_COUNT, build_ai_context
from .context_profiles import EXPANSION_CONTEXT_PROFILE
from .dsh_client import DSHClient
from .project import NovelProject
from .prompt_builder import (
    build_expansion_prompt,
    build_expansion_retry_prompt,
    build_expansion_supplement_prompt,
    build_foreshadowing_review_prompt,
)
from .task_controller import AITaskCancelled
from .history_context import history_token_budget, resolve_history_count
from .text_metrics import count_content_chars
from .token_budget import DEFAULT_INPUT_TOKEN_BUDGET


@dataclass(frozen=True)
class ExpansionRunResult:
    """Named expansion outputs that can grow without tuple breakage."""

    raw_output: str
    first_raw_output: str | None
    plain_text_fallback_count: int
    target_chars: int
    min_chars: int
    max_chars: int
    initial_char_count: int
    final_char_count: int
    supplement_attempted: bool = False
    supplement_applied: bool = False
    supplement_added_chars: int = 0
    supplement_warning: str = ""


def run_expansion(
    project: NovelProject,
    chapter_id: str,
    dsh: DSHClient,
    target_chars: int,
    history_chapters: int = EXPANSION_SUMMARY_COUNT,
    selected_foreshadowing: list[dict] | tuple[dict, ...] | None = None,
    selected_power: list[str] | tuple[str, ...] | None = None,
    cancel_event: threading.Event | None = None,
    history_mode: str = "custom",
    history_remote_enabled: bool = True,
) -> ExpansionRunResult:
    """Generate a chapter draft, retrying once when the output breaks protocol.

    ``first_raw_output`` is only set when the first attempt failed validation
    and a retry ran. The fallback count survives canonicalization so the UI
    can report protocol degradation explicitly.
    """
    target_chars = max(300, int(target_chars))
    strategy = getattr(dsh, "context_strategy", "balanced")
    history_chapters = resolve_history_count(history_mode, history_chapters, strategy)
    history_limit = history_token_budget(
        getattr(dsh, "input_token_budget", DEFAULT_INPUT_TOKEN_BUDGET), strategy,
    )
    min_chars = round(target_chars * 0.85)
    max_chars = round(target_chars * 1.15)
    prompt_budget = dsh.prompt_build_budget()

    context = build_ai_context(
        project,
        chapter_id,
        character_scope="planning",
        include_world=True,
        include_power=True,
        include_timeline=True,
        selected_power=selected_power,
        profile=EXPANSION_CONTEXT_PROFILE,
        relevance_query=json.dumps(
            list(selected_foreshadowing or ()),
            ensure_ascii=False,
            default=str,
        ),
    )
    context = replace(context, history_remote_enabled=history_remote_enabled)
    prompt = build_expansion_prompt(
        project,
        chapter_id,
        target_chars,
        summary_count=history_chapters,
        selected_foreshadowing=selected_foreshadowing,
        selected_power=selected_power,
        context=context,
        prompt_budget=prompt_budget,
        history_token_limit=history_limit,
    )
    generate_options = {"cancel_event": cancel_event} if cancel_event is not None else {}
    raw = dsh.generate(
        prompt.system_prompt,
        prompt.user_prompt,
        context_report=prompt.report,
        **generate_options,
    )
    first_raw: str | None = None
    plain_text_fallbacks = 0
    try:
        parsed = ai_protocol.parse_expansion(
            raw,
            min_chars=min_chars,
            max_chars=max_chars,
            expected_chapter_id=chapter_id,
            allowed_foreshadowing_ids={
                str(note.get("id") or "").strip()
                for note in (selected_foreshadowing or ())
                if isinstance(note, dict) and str(note.get("id") or "").strip()
            },
        )
        plain_text_fallbacks += int(parsed.plain_text_fallback)
    except ai_protocol.AIProtocolError:
        first_raw = raw
        retry_prompt = build_expansion_retry_prompt(
            project,
            chapter_id,
            target_chars=target_chars,
            summary_count=history_chapters,
            selected_foreshadowing=selected_foreshadowing,
            selected_power=selected_power,
            context=context,
            prompt_budget=prompt_budget,
            history_token_limit=history_limit,
        )
        # The retry regenerates the full chapter, so it keeps the same timeout
        # budget as the first attempt instead of a shortened one.
        raw = dsh.generate(
            retry_prompt.system_prompt,
            retry_prompt.user_prompt,
            context_report=retry_prompt.report,
            **generate_options,
        )
        try:
            parsed = ai_protocol.parse_expansion(
                raw,
                min_chars=min_chars,
                max_chars=max_chars,
                expected_chapter_id=chapter_id,
                allowed_foreshadowing_ids={
                    str(note.get("id") or "").strip()
                    for note in (selected_foreshadowing or ())
                    if isinstance(note, dict) and str(note.get("id") or "").strip()
                },
            )
            plain_text_fallbacks += int(parsed.plain_text_fallback)
        except ai_protocol.AIProtocolError:
            return ExpansionRunResult(
                raw,
                first_raw,
                plain_text_fallbacks,
                target_chars,
                min_chars,
                max_chars,
                0,
                0,
            )

    initial_char_count = parsed.char_count
    supplement_attempted = False
    supplement_applied = False
    supplement_added_chars = 0
    supplement_warning = ""
    if parsed.char_count < min_chars:
        supplement_attempted = True
        supplement_prompt = build_expansion_supplement_prompt(
            chapter_id,
            parsed.text,
            target_chars,
            min_chars=min_chars,
            max_chars=max_chars,
        )
        missing_chars = max(1, target_chars - parsed.char_count)
        try:
            supplement_raw = dsh.generate_json(
                supplement_prompt.system_prompt,
                supplement_prompt.user_prompt,
                context_report=supplement_prompt.report,
                **generate_options,
            )
            supplement = ai_protocol.parse_expansion_supplement(
                supplement_raw,
                expected_chapter_id=chapter_id,
                source_text=parsed.text,
                max_added_chars=max(300, round(missing_chars * 1.75)),
            )
            supplemented_text = _apply_expansion_insertions(
                parsed.text,
                supplement.insertions,
            )
            final_count = count_content_chars(supplemented_text)
            supplement_added_chars = max(0, final_count - parsed.char_count)
            supplement_applied = supplement_added_chars > 0
            parsed = replace(
                parsed,
                text=supplemented_text,
                char_count=final_count,
                length_ok=min_chars <= final_count <= max_chars,
            )
            if not parsed.length_ok:
                supplement_warning = (
                    "自动差额补写后仍未进入本次目标范围，请在写入前重点审阅。"
                )
        except AITaskCancelled:
            raise
        except Exception as exc:
            supplement_warning = f"自动差额补写未能安全应用：{exc}"

    selected = tuple(
        note
        for note in (selected_foreshadowing or ())
        if isinstance(note, dict) and str(note.get("id") or "").strip()
    )
    if selected:
        raw = _attach_foreshadowing_review(
            parsed,
            chapter_id,
            selected,
            dsh,
            generate_options,
        )
    else:
        raw = _canonical_expansion_output(parsed)
    return ExpansionRunResult(
        raw,
        first_raw,
        plain_text_fallbacks,
        target_chars,
        min_chars,
        max_chars,
        initial_char_count,
        parsed.char_count,
        supplement_attempted,
        supplement_applied,
        supplement_added_chars,
        supplement_warning,
    )


def _apply_expansion_insertions(
    source: str,
    insertions: tuple[ai_protocol.ExpansionInsertion, ...],
) -> str:
    """Apply validated insertions by descending source offset."""
    operations: list[tuple[int, str]] = []
    used_offsets: set[int] = set()
    for insertion in insertions:
        start = source.index(insertion.anchor)
        offset = start if insertion.position == "before" else start + len(insertion.anchor)
        if offset in used_offsets:
            raise ai_protocol.AIProtocolError("扩写补写包含冲突的插入位置。")
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


def _attach_foreshadowing_review(
    parsed: ai_protocol.ExpansionResult,
    chapter_id: str,
    selected_foreshadowing: tuple[dict, ...],
    dsh: DSHClient,
    generate_options: dict,
) -> str:
    """Append a normalized advisory block; review failure keeps prose usable."""
    prompt = build_foreshadowing_review_prompt(
        chapter_id,
        parsed.text,
        selected_foreshadowing,
    )
    try:
        review = dsh.generate_json(
            prompt.system_prompt,
            prompt.user_prompt,
            context_report=prompt.report,
            **generate_options,
        )
    except AITaskCancelled:
        raise
    except Exception:
        return _canonical_expansion_output(parsed)
    normalized = _normalize_review(review, chapter_id, selected_foreshadowing)
    if normalized is None:
        return _canonical_expansion_output(parsed)
    return _canonical_expansion_output(parsed, normalized)


def _canonical_expansion_output(
    parsed: ai_protocol.ExpansionResult,
    feedback: dict | None = None,
) -> str:
    """Rebuild protocol output from clean prose instead of nesting model raw text."""
    parts = [f"<NOVEL_TEXT>\n{parsed.text}\n</NOVEL_TEXT>"]
    if feedback is not None:
        parts.append(
            "<FORESHADOWING_FEEDBACK>\n"
            + json.dumps(feedback, ensure_ascii=False, indent=2)
            + "\n</FORESHADOWING_FEEDBACK>"
        )
    parts.append(
        f"<NOVALIST_TASK_DONE>{parsed.completion_message}</NOVALIST_TASK_DONE>"
    )
    return "\n".join(parts)


def _normalize_review(
    review: object,
    chapter_id: str,
    selected_foreshadowing: tuple[dict, ...],
) -> dict | None:
    if not isinstance(review, dict):
        return None
    if str(review.get("chapter_id") or "").strip() != chapter_id:
        return None
    items = review.get("possibly_resolved")
    if not isinstance(items, list):
        return None
    allowed_ids = {
        str(note.get("id") or "").strip() for note in selected_foreshadowing
    }
    normalized_items = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        note_id = str(item.get("foreshadowing_id") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if (
            note_id not in allowed_ids
            or note_id in seen
            or not evidence
            or not reason
        ):
            continue
        seen.add(note_id)
        normalized_items.append(
            {
                "foreshadowing_id": note_id,
                "evidence": evidence,
                "reason": reason,
            }
        )
    return {"chapter_id": chapter_id, "possibly_resolved": normalized_items}
