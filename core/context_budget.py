"""Priority-based context budget for dsh prompts.

The headless CLI only accepts the task as a command-line argument, so the
whole prompt must stay under Windows' ~30K CreateProcess limit.  Instead of
failing when a novel outgrows that, context sections are ranked by
importance: the least important ones are capped or dropped first, and every
truncation is marked in place so the model can tell trimmed data apart from
missing data.
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass

from .models import Chapter, RelatedCanon
from .project import NovelProject, chapter_number_from_id
from .project_data import ProjectDataStore

# Sections share this pool; instruction text and section labels live outside
# it.  The value leaves headroom below DSHClient's 30K hard limit.
DEFAULT_PROMPT_BUDGET = 24000

# Task-specific history windows.  The current chapter's own planning text is
# more useful for expansion than increasingly old raw prose.
EXPANSION_SUMMARY_COUNT = 5
CONTINUATION_SUMMARY_COUNT = 2
SUMMARY_PER_CHAPTER_CAP = 400

# A section trimmed below this many chars carries no useful signal; drop it.
MIN_SECTION_CHARS = 160

DROPPED_PLACEHOLDER = "（因上下文长度限制，本节内容已省略）"
HEAD_MARK = "\n…（内容过长，已截断）"
TAIL_MARK = "（前文过长，已截断）\n"

# key: (cap, keep, priority).  Lower priority number is allocated first.
SECTION_RULES: dict[str, tuple[int, str, int]] = {
    "outline": (3000, "head", 0),
    "plot_brief": (2500, "head", 0),
    "style": (1800, "head", 0),
    "content": (12000, "tail", 1),
    "selected_foreshadowing": (3500, "head", 1),
    "core_power": (1800, "head", 1),
    "core_systems": (3500, "head", 1),
    "selected_power": (3500, "head", 1),
    "state": (4000, "head", 2),
    "summaries": (2000, "head", 3),
    "characters": (3000, "head", 4),
    "future_plan": (1500, "head", 5),
    "main_arc": (1500, "head", 5),
    "timeline": (1500, "head", 6),
    "world": (2000, "head", 7),
    "power": (1500, "head", 7),
}


@dataclass(frozen=True)
class Section:
    key: str
    text: str
    cap: int
    keep: str  # "head" | "tail"
    priority: int


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
) -> AIContext:
    """Load prompt sources through one project data facade.

    The default keeps the complete canon for compatibility.  Prompt builders
    can narrow canon loading for a task so long projects do not pay the cost of
    reading every world/power file before budgeting even begins.
    """
    return build_task_context(
        project,
        chapter_id,
        character_scope=character_scope,
        include_world=include_world,
        include_power=include_power,
        include_timeline=include_timeline,
        selected_power=selected_power,
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
) -> AIContext:
    """Build an AI context with an explicit canon loading profile."""
    store = ProjectDataStore(project)
    chapter = store.load_chapter(chapter_id)
    character_query = None
    if character_scope == "planning":
        character_query = "\n".join(
            part for part in (chapter.title, chapter.outline, chapter.plot_brief) if part
        )
    core_systems = store.list_core_systems()
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
        ),
        story_state=store.load_story_state(),
        chapter_summaries=store.load_chapter_summaries(),
        main_arc=store.load_main_arc(),
        future_plan=store.load_future_plan(),
        style_guide=store.load_style_guide(),
    )


def allocate(sections: list[Section], budget: int) -> dict[str, str]:
    """Fit sections into the budget, trimming from the lowest priority up.

    Returns each section's final text.  A section that could not be kept maps
    to ``DROPPED_PLACEHOLDER`` so the model sees "omitted for length" instead
    of concluding the data does not exist.
    """
    result: dict[str, str] = {}
    remaining = max(0, int(budget))
    for section in sorted(sections, key=lambda item: item.priority):
        text = str(section.text).strip()
        if not text:
            continue
        capped_len = min(len(text), section.cap)
        if capped_len <= remaining:
            result[section.key] = (
                text if len(text) <= section.cap else _trim(text, section.cap, section.keep)
            )
            remaining -= capped_len
        elif remaining >= MIN_SECTION_CHARS:
            result[section.key] = _trim(text, remaining, section.keep)
            remaining = 0
        else:
            result[section.key] = DROPPED_PLACEHOLDER
    return result


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
        "summaries": prior_chapter_summaries(
            project,
            chapter_id,
            count=summary_count,
            per_summary_cap=SUMMARY_PER_CHAPTER_CAP,
            summaries=context.chapter_summaries,
        ),
        "characters": related.characters,
        "future_plan": context.future_plan,
        "main_arc": context.main_arc,
        "timeline": related.timeline,
        "world": related.world,
        "power": related.power,
    }
    sections: list[Section] = []
    wanted = set(keys)
    for key in SECTION_RULES:
        if key not in wanted:
            continue
        text = str(raw.get(key) or "").strip()
        if not text:
            continue
        cap, keep, priority = SECTION_RULES[key]
        if key == "summaries":
            # The setting controls the number of summaries, so let this
            # section grow with that choice. The global prompt budget still
            # trims it after higher-priority sections are allocated.
            cap = max(cap, summary_count * SUMMARY_PER_CHAPTER_CAP)
        if key == "content":
            keep = content_keep
            if content_cap is not None:
                cap = max(1, int(content_cap))
        sections.append(Section(key, text, cap, keep, priority))
    return sections


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
