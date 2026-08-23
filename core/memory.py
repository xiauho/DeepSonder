"""Memory maintenance helpers for story state and chapter summaries."""

from __future__ import annotations

from .project import NovelProject, chapter_number_from_id
from .project_data import ProjectDataStore


def merge_story_state(old_state: dict, new_state: dict) -> dict:
    """Merge dsh-generated state into the existing state.

    We keep all old character keys and overlay the updated fields, so a model
    omission does not erase existing data.
    """
    if not isinstance(old_state, dict):
        old_state = {}
    if not isinstance(new_state, dict):
        return dict(old_state)
    merged = dict(old_state)
    merged.update({key: value for key, value in new_state.items() if value is not None})

    old_chars = old_state.get("characters", {}) or {}
    new_chars = new_state.get("characters", {}) or {}
    if not isinstance(old_chars, dict):
        old_chars = {}
    if not isinstance(new_chars, dict):
        new_chars = {}
    merged_chars = dict(old_chars)
    for name, fields in new_chars.items():
        if isinstance(fields, dict):
            old_fields = merged_chars.get(name, {})
            if isinstance(old_fields, dict):
                merged_chars[name] = {**old_fields, **fields}
            else:
                merged_chars[name] = fields
        else:
            merged_chars[name] = fields
    merged["characters"] = merged_chars

    # The field means "not yet recovered". A generated empty list therefore
    # intentionally clears hooks that the chapter has resolved.
    if "foreshadowing" in new_state:
        hooks = new_state.get("foreshadowing")
        merged["foreshadowing"] = _unique_hooks(hooks)
    else:
        old_hooks = old_state.get("foreshadowing", [])
        merged["foreshadowing"] = _unique_hooks(old_hooks)

    return merged


def save_chapter_summary(project: NovelProject, chapter_id: str, summary: str) -> None:
    ProjectDataStore(project).save_chapter_summary(chapter_id, summary)


def apply_state_update(
    project: NovelProject,
    new_state: dict,
    chapter_id: str | None = None,
) -> dict:
    store = ProjectDataStore(project)
    merged = merge_state_update(store.load_story_state(), new_state, chapter_id)
    store.save_story_state(merged)
    return merged


def merge_state_update(
    old_state: dict,
    new_state: dict,
    chapter_id: str | None = None,
) -> dict:
    """Normalize and merge a generated state without performing I/O."""
    if not isinstance(new_state, dict):
        return dict(old_state) if isinstance(old_state, dict) else {}
    if chapter_id is not None:
        # Models sometimes echo the template's example value; the chapter being
        # processed is the authoritative ordinal, so pin it before merging.
        expected = chapter_number_from_id(chapter_id)
        if expected is not None and new_state.get("current_chapter") != expected:
            new_state = {**new_state, "current_chapter": expected}
    return merge_story_state(old_state, new_state)


def _unique_hooks(value: object) -> list:
    if not isinstance(value, list):
        return []
    result = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, (str, int, float)):
            continue
        key = str(item)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result
