"""Validation and persistence for AI-generated project results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import ai_protocol
from .chapter_memory import ChapterMemoryProposal, canonical_hash
from .project import NovelProject, chapter_number_from_id
from .project_data import ProjectDataStore
from .text_chunking import chapter_content_hash
from .accepted_memory import make_accepted_record


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
    def parse_consistency(
        raw: str,
        *,
        expected_chapter_id: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        report = ai_protocol.parse_consistency_report(
            raw,
            expected_chapter_id=expected_chapter_id,
        )
        return report, ai_protocol.format_consistency_report(report)

    @staticmethod
    def parse_consistency_repair(
        raw: str | dict[str, Any],
        *,
        expected_chapter_id: str,
        expected_issue_id: str,
        expected_original: str,
    ) -> ai_protocol.ConsistencyRepairResult:
        return ai_protocol.parse_consistency_repair(
            raw,
            expected_chapter_id=expected_chapter_id,
            expected_issue_id=expected_issue_id,
            expected_original=expected_original,
        )

    @staticmethod
    def commit_memory_proposal(
        project: NovelProject,
        chapter_id: str,
        proposal: ChapterMemoryProposal,
    ) -> MemoryCommitResult:
        """Commit a validated V2 proposal only while its sources are current."""
        if proposal.chapter_id != chapter_id:
            raise ValueError("记忆提案不属于当前章节。")
        if proposal.has_blockers:
            raise ValueError("记忆提案仍包含阻断冲突，不能写入故事状态。")
        chapter = project.load_chapter(chapter_id)
        if chapter_content_hash(chapter.content) != proposal.chapter_hash:
            raise ValueError("章节正文在记忆提案生成后已发生变化。")
        store = ProjectDataStore(project)
        current_state = store.load_story_state()
        if canonical_hash(current_state) != proposal.base_state_hash:
            raise ValueError("故事状态在记忆提案生成后已发生变化。")

        resulting_state = dict(proposal.resulting_state)
        if "foreshadowing" in current_state:
            resulting_state["foreshadowing"] = current_state["foreshadowing"]
        expected_chapter = chapter_number_from_id(chapter_id)
        if expected_chapter is not None:
            resulting_state["current_chapter"] = expected_chapter
        accepted_record = make_accepted_record(project, proposal, resulting_state, current_state)
        store.commit_memory_update(
            chapter_id, proposal.summary, resulting_state, accepted_record=accepted_record,
        )
        return MemoryCommitResult(
            merged_state=resulting_state,
            expected_chapter=expected_chapter,
            received_chapter=expected_chapter,
        )
