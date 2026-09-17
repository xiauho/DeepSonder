"""Current-chapter continuation orchestration."""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace

from . import ai_protocol
from .context_budget import CONTINUATION_SUMMARY_COUNT, build_ai_context
from .context_profiles import CONTINUATION_CONTEXT_PROFILE
from .dsh_client import DSHClient
from .history_context import history_token_budget, resolve_history_count
from .length_policy import assess_length
from .project import NovelProject
from .prompt_builder import (
    build_continuation_length_retry_prompt,
    build_write_prompt,
    build_write_retry_prompt,
)
from .prose_supplement import run_prose_supplement
from .task_controller import AITaskCancelled
from .text_metrics import count_content_chars
from .token_budget import DEFAULT_INPUT_TOKEN_BUDGET


MIN_CONTINUATION_CHARS = 300
MAX_CONTINUATION_CHARS = 3000
MAX_AUTOMATIC_WRITING_CORRECTIONS = 2


class ContinuationNotAvailable(ValueError):
    """Raised when the current chapter has no meaningful continuation budget."""


@dataclass(frozen=True)
class ContinuationRunResult:
    raw_output: str
    first_raw_output: str | None
    plain_text_fallback_count: int
    current_chars: int
    requested_chars: int
    target_chapter_chars: int
    run_target_chars: int = 0
    min_chars: int = 0
    max_chars: int = 0
    review_min_chars: int = 0
    review_max_chars: int = 0
    initial_generated_chars: int = 0
    final_generated_chars: int = 0
    projected_final_chars: int = 0
    length_status: str = "qualified"
    supplement_attempted: bool = False
    supplement_applied: bool = False
    supplement_added_chars: int = 0
    supplement_warning: str = ""
    supplement_attempt_count: int = 0
    length_retry_attempted: bool = False
    length_retry_applied: bool = False
    original_draft_text: str = ""
    original_draft_char_count: int = 0
    correction_history: tuple[str, ...] = ()


def continuation_target_chars(
    current_chars: int,
    target_chapter_chars: int,
    *,
    minimum: int = MIN_CONTINUATION_CHARS,
    maximum: int = MAX_CONTINUATION_CHARS,
) -> int:
    """Return one bounded continuation request or explain why it cannot run."""
    current = max(0, int(current_chars))
    target = max(minimum, int(target_chapter_chars))
    if current <= 0:
        raise ContinuationNotAvailable("当前章节正文为空，请先使用 AI 扩写或手动写下开头。")
    remaining = target - current
    if remaining <= 0:
        raise ContinuationNotAvailable(
            f"当前正文约 {current} 字，已达到目标章节字数 {target} 字。"
        )
    if remaining < minimum:
        raise ContinuationNotAvailable(
            f"当前正文约 {current} 字，距离目标章节字数只剩 {remaining} 字，"
            f"不足最小续写长度 {minimum} 字。"
        )
    return min(remaining, max(minimum, int(maximum)))


def run_continuation(
    project: NovelProject,
    chapter_id: str,
    dsh: DSHClient,
    target_chapter_chars: int = 3000,
    history_chapters: int = CONTINUATION_SUMMARY_COUNT,
    cancel_event: threading.Event | None = None,
    history_mode: str = "custom",
    history_remote_enabled: bool = True,
) -> ContinuationRunResult:
    """Continue the current chapter and retry once after a protocol failure."""
    target_chapter_chars = max(MIN_CONTINUATION_CHARS, int(target_chapter_chars))
    chapter = project.load_chapter(chapter_id)
    current_chars = count_content_chars(chapter.content)
    requested_chars = continuation_target_chars(current_chars, target_chapter_chars)
    run_target_chars = current_chars + requested_chars
    run_bounds = assess_length(run_target_chars, run_target_chars)
    strategy = getattr(dsh, "context_strategy", "balanced")
    history_chapters = resolve_history_count(history_mode, history_chapters, strategy)
    history_limit = history_token_budget(
        getattr(dsh, "input_token_budget", DEFAULT_INPUT_TOKEN_BUDGET), strategy,
    )
    prompt_budget = dsh.prompt_build_budget()
    context = build_ai_context(
        project,
        chapter_id,
        profile=CONTINUATION_CONTEXT_PROFILE,
    )
    context = replace(context, history_remote_enabled=history_remote_enabled)
    prompt = build_write_prompt(
        project,
        chapter_id,
        requested_chars,
        summary_count=history_chapters,
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
    fallback_count = 0
    automatic_corrections = 0
    generated_bounds = assess_length(requested_chars, requested_chars)
    min_chars = generated_bounds.preferred_min
    max_chars = generated_bounds.preferred_max
    try:
        parsed = ai_protocol.parse_continuation(
            raw, min_chars=min_chars, max_chars=max_chars,
        )
        fallback_count += int(parsed.plain_text_fallback)
    except ai_protocol.AIProtocolError:
        automatic_corrections += 1
        first_raw = raw
        retry = build_write_retry_prompt(
            project,
            chapter_id,
            requested_chars,
            summary_count=history_chapters,
            context=context,
            prompt_budget=prompt_budget,
            history_token_limit=history_limit,
        )
        raw = dsh.generate(
            retry.system_prompt,
            retry.user_prompt,
            context_report=retry.report,
            **generate_options,
        )
        try:
            parsed = ai_protocol.parse_continuation(
                raw, min_chars=min_chars, max_chars=max_chars,
            )
            fallback_count += int(parsed.plain_text_fallback)
        except ai_protocol.AIProtocolError:
            return ContinuationRunResult(
                raw, first_raw, fallback_count, current_chars,
                requested_chars, target_chapter_chars, run_target_chars,
                run_bounds.preferred_min, run_bounds.preferred_max,
                run_bounds.review_min, run_bounds.review_max,
            )

    initial_generated_chars = parsed.char_count
    original_draft_text = ""
    original_draft_char_count = 0
    length_retry_attempted = False
    length_retry_applied = False
    history: list[str] = []
    assessment = assess_length(current_chars + parsed.char_count, run_target_chars)
    if (
        assessment.requires_strong_confirmation
        and assessment.is_under
        and automatic_corrections < MAX_AUTOMATIC_WRITING_CORRECTIONS
    ):
        automatic_corrections += 1
        length_retry_attempted = True
        original = parsed
        retry = build_continuation_length_retry_prompt(
            prompt,
            chapter_id,
            parsed.text,
            requested_chars,
        )
        try:
            corrected_raw = dsh.generate(
                retry.system_prompt,
                retry.user_prompt,
                context_report=retry.report,
                **generate_options,
            )
            corrected = ai_protocol.parse_continuation(
                corrected_raw,
                min_chars=min_chars,
                max_chars=max_chars,
            )
            fallback_count += int(corrected.plain_text_fallback)
            if (
                corrected.char_count > original.char_count
                and abs(requested_chars - corrected.char_count)
                < abs(requested_chars - original.char_count)
            ):
                original_draft_text = original.text
                original_draft_char_count = original.char_count
                parsed = corrected
                length_retry_applied = True
                history.append(
                    f"完整篇幅纠偏：{original.char_count} → {corrected.char_count} 字，已采用纠偏稿。"
                )
            else:
                history.append(
                    f"完整篇幅纠偏未改善长度，保留 {original.char_count} 字原稿。"
                )
        except AITaskCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 - preserve the usable first fragment
            history.append(f"完整篇幅纠偏失败，已保留原稿：{exc}")

    supplement_attempted = False
    supplement_applied = False
    supplement_added_chars = 0
    supplement_warning = ""
    assessment = assess_length(current_chars + parsed.char_count, run_target_chars)
    if (
        assessment.is_under
        and automatic_corrections < MAX_AUTOMATIC_WRITING_CORRECTIONS
    ):
        automatic_corrections += 1
        supplement_attempted = True
        supplement_result = run_prose_supplement(
            chapter_id,
            parsed.text,
            requested_chars,
            dsh,
            cancel_event=cancel_event,
            task_kind="continuation_supplement",
            story_constraints=_supplement_story_constraints(context),
        )
        supplement_warning = supplement_result.warning
        if supplement_result.warning:
            history.append("局部差额补写：" + supplement_result.warning)
        if supplement_result.applied:
            supplement_applied = True
            supplement_added_chars = supplement_result.added_char_count
            history.append(
                f"局部差额补写：新增 {supplement_result.added_char_count} 字，已安全应用。"
            )
            assessment = assess_length(
                current_chars + supplement_result.final_char_count,
                run_target_chars,
            )
            parsed = replace(
                parsed,
                text=supplement_result.text,
                char_count=supplement_result.final_char_count,
                length_ok=assessment.is_qualified,
            )
            if not assessment.is_qualified:
                supplement_warning = (
                    "自动差额补写后仍未进入理想范围，可重新补写或仍然采用。"
                )
    elif assessment.is_under:
        supplement_warning = "已达到自动修正次数上限，可重新补写或仍然采用。"
        history.append(supplement_warning)

    canonical = (
        f"<NOVEL_TEXT>\n{parsed.text}\n</NOVEL_TEXT>\n"
        f"<NOVALIST_TASK_DONE>{parsed.completion_message}</NOVALIST_TASK_DONE>"
    )
    projected_final_chars = current_chars + parsed.char_count
    assessment = assess_length(projected_final_chars, run_target_chars)
    return ContinuationRunResult(
        canonical,
        first_raw,
        fallback_count,
        current_chars,
        requested_chars,
        target_chapter_chars,
        run_target_chars,
        assessment.preferred_min,
        assessment.preferred_max,
        assessment.review_min,
        assessment.review_max,
        initial_generated_chars,
        parsed.char_count,
        projected_final_chars,
        assessment.status,
        supplement_attempted,
        supplement_applied,
        supplement_added_chars,
        supplement_warning,
        int(supplement_attempted),
        length_retry_attempted,
        length_retry_applied,
        original_draft_text,
        original_draft_char_count,
        tuple(history),
    )


def _supplement_story_constraints(context) -> str:
    chapter = context.chapter
    parts = [
        f"章节：{chapter.title}",
        "本章大纲：" + (chapter.outline or "（暂无）"),
        "剧情简写：" + (chapter.plot_brief or "（暂无）"),
        "当前正文结尾：" + (chapter.content[-500:] if chapter.content else "（暂无）"),
    ]
    from .writing_style import render_style
    parts.insert(0, render_style(context.style_guide[:2500]))
    return "\n".join(parts)[:6500]
