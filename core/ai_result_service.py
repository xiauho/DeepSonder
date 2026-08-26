"""Validation and persistence for AI-generated project results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import ai_protocol, memory
from .project import NovelProject, chapter_number_from_id
from .project_data import ProjectDataStore


@dataclass(frozen=True)
class MemoryDraft:
    summary: str
    state: dict[str, Any]


@dataclass(frozen=True)
class MemoryCommitResult:
    merged_state: dict[str, Any]
    expected_chapter: int | None
    received_chapter: object

    @property
    def chapter_was_corrected(self) -> bool:
        return (
            self.expected_chapter is not None
            and self.received_chapter != self.expected_chapter
        )


class AIResultService:
    """Keep AI result parsing and project writes independent from Qt widgets."""

    @staticmethod
    def parse_expansion(
        raw: str,
        target_chars: int,
        *,
        chapter_id: str | None = None,
        selected_foreshadowing: list[dict] | tuple[dict, ...] | None = None,
    ) -> ai_protocol.ExpansionResult:
        target_chars = max(300, int(target_chars))
        allowed_ids = {
            str(note.get("id") or "").strip()
            for note in (selected_foreshadowing or ())
            if isinstance(note, dict) and str(note.get("id") or "").strip()
        }
        return ai_protocol.parse_expansion(
            raw,
            min_chars=round(target_chars * 0.85),
            max_chars=round(target_chars * 1.15),
            expected_chapter_id=chapter_id,
            allowed_foreshadowing_ids=allowed_ids,
        )

    @staticmethod
    def parse_consistency(raw: str) -> tuple[dict[str, Any], str]:
        report = ai_protocol.parse_consistency_report(raw)
        return report, ai_protocol.format_consistency_report(report)

    @staticmethod
    def prepare_memory(summary: str, new_state: object) -> MemoryDraft:
        if not isinstance(new_state, dict):
            raise ValueError("AI 返回的故事状态不是有效对象。")
        summary = str(summary or "").strip()
        if not summary:
            raise ValueError("AI 返回的章节摘要为空。")
        return MemoryDraft(summary=summary, state=dict(new_state))

    @staticmethod
    def commit_memory(
        project: NovelProject,
        chapter_id: str,
        draft: MemoryDraft,
    ) -> MemoryCommitResult:
        store = ProjectDataStore(project)
        expected_chapter = chapter_number_from_id(chapter_id)
        received_chapter = draft.state.get("current_chapter")
        # Foreshadowing is now author-owned data in memory/foreshadowing.json.
        # Keep the legacy field in the generated state for protocol
        # compatibility, but never let the old AI memory task overwrite it.
        state_without_legacy_hooks = dict(draft.state)
        state_without_legacy_hooks.pop("foreshadowing", None)
        merged_state = memory.merge_state_update(
            store.load_story_state(),
            state_without_legacy_hooks,
            chapter_id,
        )
        store.commit_memory_update(chapter_id, draft.summary, merged_state)
        return MemoryCommitResult(
            merged_state=merged_state,
            expected_chapter=expected_chapter,
            received_chapter=received_chapter,
        )
