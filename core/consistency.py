"""Consistency check orchestration."""

from __future__ import annotations

import threading

from .context_budget import build_ai_context
from .context_profiles import CONSISTENCY_CONTEXT_PROFILE, REPAIR_CONTEXT_PROFILE
from .dsh_client import DSHClient
from .project import NovelProject
from .prompt_builder import build_check_prompt, build_consistency_repair_prompt


def run_consistency_check(
    project: NovelProject,
    chapter_id: str,
    dsh: DSHClient,
    selection_mode: str = "safe",
    cancel_event: threading.Event | None = None,
) -> str:
    prompt_budget = dsh.resolve_prompt_budget(cancel_event=cancel_event)
    context = build_ai_context(
        project,
        chapter_id,
        profile=CONSISTENCY_CONTEXT_PROFILE,
        selection_mode=selection_mode,
    )
    prompt = build_check_prompt(
        project,
        chapter_id,
        context=context,
        prompt_budget=prompt_budget,
    )
    generate_options = {"cancel_event": cancel_event} if cancel_event is not None else {}
    return dsh.generate(
        prompt.system_prompt,
        prompt.user_prompt,
        context_report=prompt.report,
        **generate_options,
    )


def run_consistency_repair(
    project: NovelProject,
    chapter_id: str,
    issue: dict,
    dsh: DSHClient,
    selection_mode: str = "safe",
    cancel_event: threading.Event | None = None,
):
    prompt_budget = dsh.resolve_prompt_budget(cancel_event=cancel_event)
    context = build_ai_context(
        project,
        chapter_id,
        profile=REPAIR_CONTEXT_PROFILE,
        relevance_query=str(issue),
        selection_mode=selection_mode,
    )
    prompt = build_consistency_repair_prompt(
        project,
        chapter_id,
        issue,
        context=context,
        prompt_budget=prompt_budget,
    )
    generate_options = {"cancel_event": cancel_event} if cancel_event is not None else {}
    return dsh.generate_json(
        prompt.system_prompt,
        prompt.user_prompt,
        context_report=prompt.report,
        **generate_options,
    )
