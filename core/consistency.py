"""Consistency check orchestration."""

from __future__ import annotations

import threading

from .context_budget import build_ai_context
from .dsh_client import DSHClient
from .project import NovelProject
from .prompt_builder import build_check_prompt, build_consistency_repair_prompt


def run_consistency_check(
    project: NovelProject,
    chapter_id: str,
    dsh: DSHClient,
    cancel_event: threading.Event | None = None,
) -> str:
    context = build_ai_context(project, chapter_id)
    system_prompt, user_prompt = build_check_prompt(
        project, chapter_id, context=context
    )
    generate_options = {"cancel_event": cancel_event} if cancel_event is not None else {}
    return dsh.generate(system_prompt, user_prompt, **generate_options)


def run_consistency_repair(
    project: NovelProject,
    chapter_id: str,
    issue: dict,
    dsh: DSHClient,
    cancel_event: threading.Event | None = None,
):
    context = build_ai_context(project, chapter_id)
    system_prompt, user_prompt = build_consistency_repair_prompt(
        project, chapter_id, issue, context=context
    )
    generate_options = {"cancel_event": cancel_event} if cancel_event is not None else {}
    return dsh.generate_json(system_prompt, user_prompt, **generate_options)
