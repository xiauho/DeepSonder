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
    build_foreshadowing_review_prompt,
)
from .task_controller import AITaskCancelled
from .history_context import history_token_budget, resolve_history_count
from .token_budget import DEFAULT_INPUT_TOKEN_BUDGET


@dataclass(frozen=True)
class ExpansionRunResult:
    """Named expansion outputs that can grow without tuple breakage."""

    raw_output: str
    first_raw_output: str | None
    plain_text_fallback_count: int


def run_expansion(
    project: NovelProject,
    chapter_id: str,
    dsh: DSHClient,
    target_chars: int = 2000,
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
            )
            plain_text_fallbacks += int(parsed.plain_text_fallback)
        except ai_protocol.AIProtocolError:
            return ExpansionRunResult(raw, first_raw, plain_text_fallbacks)

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
    return ExpansionRunResult(raw, first_raw, plain_text_fallbacks)


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
