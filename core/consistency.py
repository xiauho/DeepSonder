"""Consistency check orchestration."""

from __future__ import annotations

import threading

from .dsh_client import DSHClient
from .project import NovelProject
from .prompt_builder import build_check_prompt


def run_consistency_check(
    project: NovelProject,
    chapter_id: str,
    dsh: DSHClient,
    cancel_event: threading.Event | None = None,
) -> str:
    system_prompt, user_prompt = build_check_prompt(project, chapter_id)
    generate_options = {"cancel_event": cancel_event} if cancel_event is not None else {}
    return dsh.generate(system_prompt, user_prompt, **generate_options)
