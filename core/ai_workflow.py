"""UI-independent orchestration for Novalist AI tasks."""

from __future__ import annotations

import threading
from pathlib import Path

from . import consistency, continuation, expansion
from .continuation import ContinuationRunResult
from .expansion import ExpansionRunResult
from .prose_supplement import ProseSupplementRunResult, run_prose_supplement
from .chapter_facts import (
    ChapterFactLedger,
    FactLedgerCache,
    extract_chapter_fact_ledger,
)
from .chapter_memory import (
    ChapterMemoryCache,
    ChapterMemoryProposal,
    canonical_hash,
    generate_chapter_memory_proposal,
)
from .accepted_memory import memory_base_state_for
from .context_budget import build_ai_context
from .context_profiles import SUMMARY_CONTEXT_PROFILE
from .dsh_client import DSHClient
from .project import NovelProject
from .token_budget import (
    DEFAULT_CHUNK_OVERLAP_TOKENS,
    DEFAULT_CHUNK_TOKEN_BUDGET,
    DEFAULT_INPUT_TOKEN_BUDGET,
    DEFAULT_TOKEN_ESTIMATOR,
)
from .text_chunking import chapter_content_hash


class AIWorkflowService:
    """Run complete AI workflows against one captured DSH client.

    The service owns prompt sequencing and protocol parsing that do not depend
    on Qt. The caller remains responsible for threading and user confirmation.
    """

    def __init__(
        self,
        dsh: DSHClient,
        *,
        input_token_budget: int | None = None,
        chunk_token_budget: int = DEFAULT_CHUNK_TOKEN_BUDGET,
        chunk_overlap_tokens: int = DEFAULT_CHUNK_OVERLAP_TOKENS,
        fact_cache_root: Path | None = None,
        memory_cache_root: Path | None = None,
    ):
        self.dsh = dsh
        self.input_token_budget = int(
            input_token_budget
            if input_token_budget is not None
            else getattr(dsh, "input_token_budget", DEFAULT_INPUT_TOKEN_BUDGET)
        )
        self.chunk_token_budget = max(1_000, int(chunk_token_budget))
        self.chunk_overlap_tokens = max(0, int(chunk_overlap_tokens))
        self.fact_cache_root = Path(fact_cache_root) if fact_cache_root else None
        self.memory_cache_root = Path(memory_cache_root) if memory_cache_root else None

    def expand(
        self,
        project: NovelProject,
        chapter_id: str,
        target_chars: int,
        history_chapters: int = 5,
        selected_foreshadowing: list[dict] | tuple[dict, ...] | None = None,
        selected_power: list[str] | tuple[str, ...] | None = None,
        cancel_event: threading.Event | None = None,
        history_mode: str = "custom",
        history_remote_enabled: bool = True,
    ) -> ExpansionRunResult:
        return expansion.run_expansion(
            project,
            chapter_id,
            self.dsh,
            target_chars=target_chars,
            history_chapters=history_chapters,
            history_mode=history_mode,
            history_remote_enabled=history_remote_enabled,
            selected_foreshadowing=selected_foreshadowing,
            selected_power=selected_power,
            cancel_event=cancel_event,
        )

    def continue_chapter(
        self,
        project: NovelProject,
        chapter_id: str,
        target_chapter_chars: int = 3000,
        history_chapters: int = 5,
        cancel_event: threading.Event | None = None,
        history_mode: str = "custom",
        history_remote_enabled: bool = True,
    ) -> ContinuationRunResult:
        return continuation.run_continuation(
            project,
            chapter_id,
            self.dsh,
            target_chapter_chars=target_chapter_chars,
            history_chapters=history_chapters,
            history_mode=history_mode,
            history_remote_enabled=history_remote_enabled,
            cancel_event=cancel_event,
        )

    def supplement_prose(
        self,
        chapter_id: str,
        prose: str,
        target_chars: int,
        cancel_event: threading.Event | None = None,
        *,
        task_kind: str = "prose_length_supplement",
        story_constraints: str = "",
    ) -> ProseSupplementRunResult:
        return run_prose_supplement(
            chapter_id,
            prose,
            target_chars,
            self.dsh,
            cancel_event=cancel_event,
            task_kind=task_kind,
            story_constraints=story_constraints,
        )

    def check(
        self,
        project: NovelProject,
        chapter_id: str,
        cancel_event: threading.Event | None = None,
        *,
        history_remote_enabled: bool = True,
    ) -> str:
        return consistency.run_consistency_check(
            project,
            chapter_id,
            self.dsh,
            cancel_event=cancel_event,
            history_remote_enabled=history_remote_enabled,
        )

    def repair_consistency(
        self,
        project: NovelProject,
        chapter_id: str,
        issue: dict,
        cancel_event: threading.Event | None = None,
    ):
        return consistency.run_consistency_repair(
            project,
            chapter_id,
            issue,
            self.dsh,
            cancel_event=cancel_event,
        )

    def build_chapter_fact_ledger(
        self,
        project: NovelProject,
        chapter_id: str,
        cancel_event: threading.Event | None = None,
    ) -> ChapterFactLedger:
        """Build the new validated fact layer without changing project memory."""
        cache = FactLedgerCache(project, self.fact_cache_root)
        estimator = getattr(self.dsh, "token_estimator", DEFAULT_TOKEN_ESTIMATOR)
        return extract_chapter_fact_ledger(
            project,
            chapter_id,
            self.dsh,
            input_token_budget=self.input_token_budget,
            chunk_token_budget=self.chunk_token_budget,
            overlap_tokens=self.chunk_overlap_tokens,
            estimator=estimator,
            cache=cache,
            cancel_event=cancel_event,
        )

    def update_memory(
        self,
        project: NovelProject,
        chapter_id: str,
        cancel_event: threading.Event | None = None,
        *,
        force_refresh: bool = False,
    ) -> ChapterMemoryProposal:
        """Build an evidence-bound summary and locally applied memory patch."""
        ledger = self.build_chapter_fact_ledger(
            project,
            chapter_id,
            cancel_event=cancel_event,
        )
        context = build_ai_context(
            project,
            chapter_id,
            profile=SUMMARY_CONTEXT_PROFILE,
            relevance_query="\n".join(
                item.subject for item in ledger.facts if item.subject.strip()
            ),
        )
        if chapter_content_hash(context.chapter.content) != ledger.chapter_hash:
            raise RuntimeError("章节正文在事实提取期间发生变化，请重新运行记忆更新。")
        source_state = project.load_story_state()
        base_state, base_state_scope = memory_base_state_for(
            project,
            chapter_id,
            source_state,
        )
        estimator = getattr(self.dsh, "token_estimator", DEFAULT_TOKEN_ESTIMATOR)
        return generate_chapter_memory_proposal(
            project,
            ledger,
            self.dsh,
            base_state=base_state,
            canon_context=context.related.to_block(),
            input_token_budget=self.input_token_budget,
            estimator=estimator,
            cache=ChapterMemoryCache(project, self.memory_cache_root),
            cancel_event=cancel_event,
            base_state_scope=base_state_scope,
            source_state_hash=canonical_hash(source_state),
            force_refresh=force_refresh,
        )
