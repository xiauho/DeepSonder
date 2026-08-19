"""Consistency check orchestration."""

from __future__ import annotations

from .dsh_client import DSHClient
from .project import NovelProject
from .prompt_builder import build_check_prompt


def run_consistency_check(
    project: NovelProject,
    chapter_id: str,
    dsh: DSHClient,
) -> str:
    system_prompt, user_prompt = build_check_prompt(project, chapter_id)
    return dsh.generate(system_prompt, user_prompt)
