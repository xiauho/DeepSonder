"""Memory maintenance helpers for story state and chapter summaries."""

from __future__ import annotations

from .project import NovelProject


def merge_story_state(old_state: dict, new_state: dict) -> dict:
    """Merge dsh-generated state into the existing state.

    We keep all old character keys and overlay the updated fields, so a model
    omission does not erase existing data.
    """
    merged = dict(old_state)
    merged.update(new_state)

    old_chars = old_state.get("characters", {}) or {}
    new_chars = new_state.get("characters", {}) or {}
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

    # Combine foreshadowing without duplicates.
    old_foreshadowing = list(old_state.get("foreshadowing", []) or [])
    new_foreshadowing = list(new_state.get("foreshadowing", []) or [])
    combined = list(dict.fromkeys(old_foreshadowing + new_foreshadowing))
    merged["foreshadowing"] = combined

    return merged


def save_chapter_summary(project: NovelProject, chapter_id: str, summary: str) -> None:
    summaries = project.load_chapter_summaries()
    summaries[chapter_id] = summary.strip()
    project.save_chapter_summaries(summaries)


def apply_state_update(project: NovelProject, new_state: dict) -> dict:
    old_state = project.load_story_state()
    merged = merge_story_state(old_state, new_state)
    project.save_story_state(merged)
    return merged
