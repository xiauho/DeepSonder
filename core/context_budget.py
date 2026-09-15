"""Priority-based context budgets for file-bridged dsh business prompts.

The headless CLI receives only a short loader as a positional argument. The
business prompt is compiled into a verified isolated task file. Context
sections are ranked by importance: the least important ones are capped or
dropped first, and every truncation is marked in place.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass

from .context_profiles import ContextProfile, DEFAULT_CONTEXT_PROFILE
from .context_report import SectionUsage
from .models import Chapter, RelatedCanon
from .project import NovelProject, chapter_number_from_id
from .project_data import ProjectDataStore
from .history_context import HistoryWindow, HistoryAllocation, select_history_window, allocate_history
from .accepted_memory import AcceptedMemoryView

# Sections share this pool; instruction text and section labels live outside
# it. DSHClient may lower this default further to keep the compiled prompt
# below its configured token hard limit.
DEFAULT_PROMPT_BUDGET = 24000

# Task-specific history windows.  The current chapter's own planning text is
# more useful for expansion than increasingly old raw prose.
EXPANSION_SUMMARY_COUNT = 5
CONTINUATION_SUMMARY_COUNT = 2

# A section trimmed below this many chars carries no useful signal; drop it.
MIN_SECTION_CHARS = 160

DROPPED_PLACEHOLDER = "（因上下文长度限制，本节内容已省略）"
HEAD_MARK = "\n…（内容过长，已截断）"
TAIL_MARK = "（前文过长，已截断）\n"

# key: (cap, keep, priority).  Lower priority number is allocated first.
SECTION_RULES: dict[str, tuple[int, str, int]] = {
    "outline": (4000, "head", 0),
    "plot_brief": (3000, "head", 0),
    "style": (2500, "head", 0),
    "content": (12000, "tail", 1),
    "selected_foreshadowing": (5000, "head", 1),
    "core_power": (2500, "head", 1),
    "core_systems": (5000, "head", 1),
    "selected_power": (5000, "head", 1),
    "state": (6000, "head", 2),
    # Summaries are rendered chronologically, so trimming must keep the tail:
    # the chapters nearest to the current one carry the strongest continuity.
    "summaries": (6000, "tail", 3),
    "characters": (6000, "head", 4),
    "future_plan": (2500, "head", 5),
    "main_arc": (2500, "head", 5),
    "timeline": (4000, "head", 6),
    "world": (8000, "head", 7),
    "power": (6000, "head", 7),
}


@dataclass(frozen=True)
class Section:
    key: str
    text: str
    cap: int
    keep: str  # "head" | "tail"
    priority: int
    history: HistoryWindow | None = None


@dataclass(frozen=True)
class AllocationResult:
    """Allocated section text plus redacted per-section usage metrics."""

    values: dict[str, str]
    sections: tuple[SectionUsage, ...]
    budget: int
    history: HistoryAllocation | None = None


@dataclass(frozen=True)
class AIContext:
    """One immutable-in-use snapshot of all source data for an AI task."""

    project_root: str
    chapter_id: str
    chapter: Chapter
    related: RelatedCanon
    story_state: dict
    chapter_summaries: dict
    main_arc: str
    future_plan: str
    style_guide: str
    history_view: AcceptedMemoryView | None = None
    history_query: str = ""
    history_remote_enabled: bool = True
    state_scope: str = ""

    def fingerprint(self, editor_text: str | None = None) -> str:
        payload = {
            "project_root": self.project_root,
            "chapter_id": self.chapter_id,
            "chapter": {
                "title": self.chapter.title,
                "outline": self.chapter.outline,
                "plot_brief": self.chapter.plot_brief,
                "content": self.chapter.content,
                "raw": self.chapter.raw,
            },
            "related": {
                "characters": self.related.characters,
                "world": self.related.world,
                "power": self.related.power,
                "core_power": self.related.core_power,
                "core_systems": self.related.core_systems,
                "selected_power": self.related.selected_power,
                "timeline": self.related.timeline,
            },
            "story_state": self.story_state,
            "chapter_summaries": self.chapter_summaries,
            "main_arc": self.main_arc,
            "future_plan": self.future_plan,
            "style_guide": self.style_guide,
            "editor_text": editor_text,
            "accepted_memory": self.history_view.records if self.history_view else {},
            "history_remote_enabled": self.history_remote_enabled,
            "state_scope": self.state_scope,
        }
        canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_ai_context(
    project: NovelProject,
    chapter_id: str,
    *,
    character_scope: str = "chapter",
    include_world: bool = True,
    include_power: bool = True,
    include_timeline: bool = True,
    selected_power: list[str] | tuple[str, ...] | None = None,
    profile: ContextProfile | None = None,
    relevance_query: str | None = None,
) -> AIContext:
    """Load prompt sources through one project data facade.

    Prompt builders can narrow canon loading for a task so long projects do not
    pay the cost of reading every world/power file before budgeting even begins.
    """
    return build_task_context(
        project,
        chapter_id,
        character_scope=character_scope,
        include_world=include_world,
        include_power=include_power,
        include_timeline=include_timeline,
        selected_power=selected_power,
        profile=profile,
        relevance_query=relevance_query,
    )


def build_task_context(
    project: NovelProject,
    chapter_id: str,
    *,
    character_scope: str = "chapter",
    include_world: bool = True,
    include_power: bool = True,
    include_timeline: bool = True,
    selected_power: list[str] | tuple[str, ...] | None = None,
    profile: ContextProfile | None = None,
    relevance_query: str | None = None,
) -> AIContext:
    """Build an AI context with an explicit canon loading profile."""
    profile = profile or DEFAULT_CONTEXT_PROFILE
    character_scope = profile.character_scope
    include_world = profile.include_world
    include_power = profile.include_power
    include_timeline = profile.include_timeline
    store = ProjectDataStore(project)
    chapter = store.load_chapter(chapter_id)
    story_state = store.load_story_state() if profile.include_story_state else {}
    summaries = store.load_chapter_summaries() if profile.include_summaries else {}
    history_view = AcceptedMemoryView(project, summaries) if profile.include_summaries else None
    state_scope = ""
    if history_view is not None and profile.include_story_state:
        story_state, state_scope = history_view.state_for(chapter_id, story_state)
    character_query = None
    if character_scope == "planning":
        character_query = "\n".join(
            part for part in (chapter.title, chapter.outline, chapter.plot_brief) if part
        )
    elif character_scope == "none":
        character_query = ""
    elif character_scope == "relevance":
        character_query = ""
    extra_relevance = str(relevance_query or "")
    if profile.relevance_scope == "planning":
        relevance_query = "\n".join(
            part for part in (chapter.title, chapter.outline, chapter.plot_brief) if part
        )
    elif profile.relevance_scope == "chapter":
        relevance_query = chapter.raw
    elif profile.relevance_scope == "chapter_state":
        relevance_query = chapter.raw + "\n" + json.dumps(
            story_state,
            ensure_ascii=False,
            default=str,
        )
    else:
        relevance_query = ""
    if extra_relevance:
        relevance_query = f"{relevance_query}\n{extra_relevance}".strip()
        if character_query is not None:
            character_query = f"{character_query}\n{extra_relevance}".strip()
    if character_scope == "relevance":
        character_query = relevance_query
    core_systems = store.list_core_systems() if include_power else []
    selected_systems = tuple(core_systems) + tuple(selected_power or ())
    return AIContext(
        project_root=str(store.root.resolve()).casefold(),
        chapter_id=str(chapter_id),
        chapter=chapter,
        related=store.find_related_canon(
            chapter_id,
            character_query=character_query,
            include_world=include_world,
            include_power=include_power,
            include_timeline=include_timeline,
            selected_power=selected_systems,
            core_power_paths=core_systems,
            relevance_query=relevance_query,
        ),
        story_state=story_state,
        chapter_summaries=summaries,
        main_arc=store.load_main_arc() if profile.include_main_arc else "",
        future_plan=store.load_future_plan() if profile.include_future_plan else "",
        style_guide=store.load_style_guide() if profile.include_style else "",
        history_view=history_view,
        history_query=relevance_query,
        state_scope=state_scope,
    )


def allocate(sections: list[Section], budget: int) -> dict[str, str]:
    """Fit sections into the budget, trimming from the lowest priority up.

    Returns each section's final text.  A section that could not be kept maps
    to ``DROPPED_PLACEHOLDER`` so the model sees "omitted for length" instead
    of concluding the data does not exist.
    """
    return allocate_with_report(sections, budget).values


def allocate_with_report(
    sections: list[Section], budget: int, *, history_token_limit: int | None = None,
) -> AllocationResult:
    """Allocate sections and record counts without retaining their text."""
    result: dict[str, str] = {}
    usage: list[SectionUsage] = []
    normalized_budget = max(0, int(budget))
    remaining = normalized_budget
    history_result = None
    # Reserve task text, core rules, characters and state before history.
    # Lower-priority background can use the space left by the history pool.
    for section in sorted(sections, key=lambda item: 4.5 if item.history is not None else item.priority):
        if section.history is not None:
            history_result = allocate_history(
                section.history, remaining,
                history_token_limit if history_token_limit is not None else int(budget * 0.2),
            )
            rendered = history_result.text
            if rendered:
                result[section.key] = rendered
            elif section.history.entries or section.history.remote:
                result[section.key] = DROPPED_PLACEHOLDER
            remaining -= len(rendered)
            status = (
                "dropped" if (section.history.entries or section.history.remote) and not rendered
                else "trimmed" if history_result.excluded_budget else "full"
            )
            usage.append(SectionUsage(
                section.key, len(section.text), len(rendered), status,
                section.priority, "whole_entries",
            ))
            continue
        text = str(section.text).strip()
        if not text:
            continue
        capped_len = min(len(text), section.cap)
        if capped_len <= remaining:
            rendered = (
                text if len(text) <= section.cap else _trim(text, section.cap, section.keep)
            )
            result[section.key] = rendered
            status = "full" if len(text) <= section.cap else "capped"
            remaining -= capped_len
        elif remaining >= MIN_SECTION_CHARS:
            rendered = _trim(text, remaining, section.keep)
            result[section.key] = rendered
            status = "trimmed"
            remaining = 0
        else:
            rendered = DROPPED_PLACEHOLDER
            result[section.key] = rendered
            status = "dropped"
        usage.append(
            SectionUsage(
                key=section.key,
                source_chars=len(text),
                sent_chars=0 if status == "dropped" else len(rendered),
                status=status,
                priority=section.priority,
                keep=section.keep,
            )
        )
    return AllocationResult(result, tuple(usage), normalized_budget, history_result)


def gather_sections(
    project: NovelProject,
    chapter_id: str,
    keys,
    *,
    content_keep: str = "tail",
    content_cap: int | None = None,
    summary_count: int = EXPANSION_SUMMARY_COUNT,
    selected_foreshadowing: list[dict] | tuple[dict, ...] | None = None,
    selected_power: list[str] | tuple[str, ...] | None = None,
    context: AIContext | None = None,
) -> list[Section]:
    """Load the named standard sections in deterministic rule order."""
    context = context or build_ai_context(
        project,
        chapter_id,
        selected_power=selected_power,
    )
    chapter = context.chapter
    related = context.related
    summary_count = max(0, int(summary_count))
    keys = set(keys)
    history = select_history_window(
        context.chapter_summaries, [path.stem for path in project.list_chapters()],
        chapter_id, summary_count,
        view=context.history_view, query=context.history_query,
        remote_enabled=context.history_remote_enabled,
    ) if "summaries" in keys else None
    raw = {
        "outline": chapter.outline,
        "plot_brief": chapter.plot_brief,
        "content": chapter.content,
        "style": context.style_guide,
        "selected_foreshadowing": render_selected_foreshadowing(selected_foreshadowing),
        "core_power": related.core_power,
        "core_systems": related.core_systems,
        "selected_power": related.selected_power,
        "state": json.dumps(
            compact_story_state(context.story_state),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "summaries": "\n\n".join(entry.rendered for entry in (*history.entries, *history.remote)) if history else "",
        "characters": related.characters,
        "future_plan": context.future_plan,
        "main_arc": context.main_arc,
        "timeline": related.timeline,
        "world": related.world,
        "power": related.power,
    }
    if context.state_scope:
        raw["state"] = f"记忆适用范围：{state_scope_label(context.state_scope)}\n" + raw["state"]
    sections: list[Section] = []
    wanted = set(keys)
    for key in SECTION_RULES:
        if key not in wanted:
            continue
        text = str(raw.get(key) or "").strip()
        if not text and key != "summaries":
            continue
        cap, keep, priority = SECTION_RULES[key]
        if key == "summaries":
            # History is allocated as whole entries under its own token pool.
            cap = len(text)
        if key == "content":
            keep = content_keep
            if content_cap is not None:
                cap = max(1, int(content_cap))
        sections.append(Section(key, text, cap, keep, priority, history if key == "summaries" else None))
    return sections


def state_scope_label(scope: str) -> str:
    if scope.startswith("snapshot:"):
        return f"使用 {scope.split(':', 1)[1]} 的已采用历史状态快照"
    return {
        "legacy_unverified": "旧状态的版本未确认，仅供参考",
        "unknown_position": "章节顺序无法确认，未提供全局故事状态",
        "future_or_unverified_state_omitted": "缺少适用于本章的有效状态快照，已省略全局故事状态",
    }.get(scope, "版本未确认")


def render_selected_foreshadowing(notes: object) -> str:
    """Render only author-selected notes with a bounded per-note payload."""
    if not isinstance(notes, (list, tuple)):
        return ""
    rendered: list[str] = []
    for note in notes[:8]:
        if not isinstance(note, dict):
            continue
        note_id = str(note.get("id") or "").strip()
        title = str(note.get("title") or "未命名伏笔").strip()
        description = str(note.get("note") or "暂无说明").strip()
        characters = ", ".join(str(item) for item in note.get("related_characters") or [])
        lines = [f"- 伏笔 ID：{note_id}", f"  标题：{title}", f"  说明：{description}"]
        metadata = [
            ("首次出现", note.get("first_seen_chapter")),
            ("最近出现", note.get("recent_seen_chapter")),
            ("计划回收", note.get("planned_resolution_chapter")),
            ("优先级", note.get("priority")),
            ("关联人物", characters),
        ]
        for label, value in metadata:
            if str(value or "").strip():
                lines.append(f"  {label}：{value}")
        rendered.append("\n".join(lines)[:700])
    return "\n\n".join(rendered)[:3500]


def compact_story_state(
    state: dict,
    *,
    value_cap: int = 200,
    hook_cap: int = 120,
    max_list_items: int = 12,
    max_hooks: int = 24,
) -> dict:
    """Bound a story state for prompting without breaking its structure.

    Capped strings end with "…" so prompts can tell the model not to copy a
    truncated value back verbatim.
    """
    if not isinstance(state, dict):
        return {}
    compacted: dict[str, object] = {}
    for key, value in state.items():
        if key == "foreshadowing" and isinstance(value, list):
            compacted[key] = [_cap_text(item, hook_cap) for item in value[:max_hooks]]
        else:
            compacted[key] = _cap_value(value, value_cap, max_list_items)
    return compacted


def prior_chapter_summaries(
    project: NovelProject,
    chapter_id: str,
    *,
    count: int = 5,
    per_summary_cap: int = 400,
    summaries: dict | None = None,
) -> str:
    """Recent summaries that cannot spoil chapters after the current one.

    Chapters sort numerically (chapter_2 before chapter_10); summaries of
    later chapters are excluded so regenerating an early chapter does not
    feed the model future plot.
    """
    if count <= 0:
        return ""
    current = chapter_number_from_id(chapter_id)
    entries: list[tuple[int, int, str, str]] = []
    source = summaries if summaries is not None else project.load_chapter_summaries()
    for key, value in (source or {}).items():
        text = str(value).strip()
        if not text or key == chapter_id:
            continue
        number = chapter_number_from_id(key)
        if current is not None and number is not None and number > current:
            continue
        entries.append((0 if number is None else 1, number or 0, key, text))
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    recent = entries[-count:]
    return "\n\n".join(
        f"### {key}\n{_cap_text(text, per_summary_cap)}" for _rank, _number, key, text in recent
    )


def _trim(text: str, allowance: int, keep: str) -> str:
    if keep == "tail":
        return TAIL_MARK + text[-(allowance - len(TAIL_MARK)) :]
    if keep == "head_tail":
        marker = "\n…（正文中段已截断，保留章节开头与最近结尾）\n"
        available = max(2, allowance - len(marker))
        head_chars = max(1, available // 7)
        tail_chars = max(1, available - head_chars)
        return text[:head_chars] + marker + text[-tail_chars:]
    return text[: allowance - len(HEAD_MARK)] + HEAD_MARK


def _cap_value(value: object, cap: int, max_items: int) -> object:
    if isinstance(value, str):
        return _cap_text(value, cap)
    if isinstance(value, list):
        return [_cap_value(item, cap, max_items) for item in value[:max_items]]
    if isinstance(value, dict):
        return {str(key): _cap_value(item, cap, max_items) for key, item in value.items()}
    return value


def _cap_text(value: object, cap: int) -> str:
    text = str(value).strip()
    if len(text) <= cap:
        return text
    return text[:cap].rstrip() + "…"
