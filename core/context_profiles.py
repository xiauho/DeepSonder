"""Task-specific source loading profiles for AI context construction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextProfile:
    """Declare which project sources one AI task is allowed to load."""

    task_kind: str
    character_scope: str = "chapter"  # chapter | planning | relevance | none
    relevance_scope: str = "chapter_state"  # planning | chapter | chapter_state | none
    include_world: bool = True
    include_power: bool = True
    include_timeline: bool = True
    include_story_state: bool = True
    include_summaries: bool = True
    include_main_arc: bool = True
    include_future_plan: bool = True
    include_style: bool = True


DEFAULT_CONTEXT_PROFILE = ContextProfile("default")

EXPANSION_CONTEXT_PROFILE = ContextProfile(
    "chapter_expansion",
    character_scope="planning",
    relevance_scope="planning",
)

CONTINUATION_CONTEXT_PROFILE = ContextProfile("continuation_current_chapter")

CONSISTENCY_CONTEXT_PROFILE = ContextProfile(
    "consistency_check",
    include_future_plan=False,
    include_style=False,
)

REPAIR_CONTEXT_PROFILE = ContextProfile(
    "consistency_repair",
    character_scope="relevance",
    relevance_scope="none",
    include_summaries=False,
    include_main_arc=False,
    include_future_plan=False,
    include_style=False,
)

SUMMARY_CONTEXT_PROFILE = ContextProfile(
    "chapter_summary",
    relevance_scope="chapter",
    include_summaries=False,
    include_main_arc=False,
    include_future_plan=False,
    include_style=False,
)
