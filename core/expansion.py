"""Chapter expansion orchestration."""

from __future__ import annotations

import threading

from . import ai_protocol
from .context_budget import EXPANSION_SUMMARY_COUNT, build_ai_context
from .dsh_client import DSHClient
from .project import NovelProject
from .prompt_builder import build_expansion_prompt, build_expansion_retry_prompt


def run_expansion(
    project: NovelProject,
    chapter_id: str,
    dsh: DSHClient,
    target_chars: int = 2000,
    history_chapters: int = EXPANSION_SUMMARY_COUNT,
    cancel_event: threading.Event | None = None,
) -> tuple[str, str | None]:
    """Generate a chapter draft, retrying once when the output breaks protocol.

    Returns ``(raw_output, first_raw)``.  ``first_raw`` is only set when the
    first attempt failed the protocol check and a retry ran; the UI keeps it
    in the task log so a near-miss can still be salvaged by hand.
    """
    target_chars = max(300, int(target_chars))
    history_chapters = max(0, int(history_chapters))
    min_chars = round(target_chars * 0.85)
    max_chars = round(target_chars * 1.15)

    context = build_ai_context(
        project,
        chapter_id,
        character_scope="planning",
        include_world=False,
        include_power=False,
        include_timeline=True,
    )
    system_prompt, user_prompt = build_expansion_prompt(
        project,
        chapter_id,
        target_chars,
        summary_count=history_chapters,
        context=context,
    )
    generate_options = {"cancel_event": cancel_event} if cancel_event is not None else {}
    raw = dsh.generate(system_prompt, user_prompt, **generate_options)
    try:
        ai_protocol.parse_expansion(raw, min_chars=min_chars, max_chars=max_chars)
    except ai_protocol.AIProtocolError:
        retry_system, retry_user = build_expansion_retry_prompt(
            project,
            chapter_id,
            target_chars=target_chars,
            summary_count=history_chapters,
            context=context,
        )
        # The retry regenerates the full chapter, so it keeps the same timeout
        # budget as the first attempt instead of a shortened one.
        retry_raw = dsh.generate(retry_system, retry_user, **generate_options)
        return retry_raw, raw
    return raw, None
