"""UI-independent orchestration for Novalist AI tasks."""

from __future__ import annotations

import threading

from . import ai_protocol, consistency, expansion
from .context_budget import build_ai_context
from .dsh_client import DSHClient
from .project import NovelProject
from .prompt_builder import build_state_update_prompt, build_summary_prompt


class AIWorkflowService:
    """Run complete AI workflows against one captured DSH client.

    The service owns prompt sequencing and protocol parsing that do not depend
    on Qt. The caller remains responsible for threading and user confirmation.
    """

    def __init__(self, dsh: DSHClient):
        self.dsh = dsh

    def expand(
        self,
        project: NovelProject,
        chapter_id: str,
        target_chars: int = 2000,
        history_chapters: int = 5,
        selected_foreshadowing: list[dict] | tuple[dict, ...] | None = None,
        selected_power: list[str] | tuple[str, ...] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, str | None]:
        return expansion.run_expansion(
            project,
            chapter_id,
            self.dsh,
            target_chars=target_chars,
            history_chapters=history_chapters,
            selected_foreshadowing=selected_foreshadowing,
            selected_power=selected_power,
            cancel_event=cancel_event,
        )

    def check(
        self,
        project: NovelProject,
        chapter_id: str,
        cancel_event: threading.Event | None = None,
    ) -> str:
        return consistency.run_consistency_check(project, chapter_id, self.dsh, cancel_event)

    def repair_consistency(
        self,
        project: NovelProject,
        chapter_id: str,
        issue: dict,
        cancel_event: threading.Event | None = None,
    ):
        return consistency.run_consistency_repair(
            project, chapter_id, issue, self.dsh, cancel_event
        )

    def update_memory(
        self,
        project: NovelProject,
        chapter_id: str,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, dict, str]:
        prompt_budget = self.dsh.resolve_prompt_budget(cancel_event=cancel_event)
        context = build_ai_context(project, chapter_id)
        summary_prompt = build_summary_prompt(
            project,
            chapter_id,
            context=context,
            prompt_budget=prompt_budget,
        )
        generate_options = {"cancel_event": cancel_event} if cancel_event is not None else {}
        summary_raw = self.dsh.generate(
            summary_prompt.system_prompt,
            summary_prompt.user_prompt,
            context_report=summary_prompt.report,
            **generate_options,
        )
        summary_result = ai_protocol.parse_summary_result(summary_raw)

        state_prompt = build_state_update_prompt(
            project,
            chapter_id,
            context=context,
            prompt_budget=prompt_budget,
        )
        state_raw = self.dsh.generate_json(
            state_prompt.system_prompt,
            state_prompt.user_prompt,
            context_report=state_prompt.report,
            **generate_options,
        )
        state_result = ai_protocol.parse_story_state_result(state_raw)

        completion = (
            f"{summary_result.completion_message}；"
            f"{state_result.completion_message}"
        )
        return summary_result.text, state_result.state, completion
