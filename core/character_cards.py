"""Helpers for reconciling tracked story characters with canon cards."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .character_card_sync import replace_managed_state
from .project import NovelProject
from .project_data import CANON_ENTRY_TYPES, ProjectDataStore, sanitize_filename


@dataclass(frozen=True)
class CharacterCardCandidate:
    """One tracked character that does not have a canon card yet."""

    name: str
    details: dict[str, Any]
    path: Path


def missing_character_cards(
    project: NovelProject,
    story_state: object,
) -> list[CharacterCardCandidate]:
    """Find tracked characters whose names are absent from canon cards.

    Character state is intentionally not treated as a card automatically: AI
    state can contain temporary people, aliases, or groups. This helper only
    prepares explicit user-confirmed draft candidates.
    """
    if not isinstance(story_state, dict):
        return []
    characters = story_state.get("characters")
    if not isinstance(characters, dict):
        return []

    used_stems = {path.stem.casefold() for path in project.list_characters()}
    candidates: list[CharacterCardCandidate] = []
    for raw_name, raw_details in characters.items():
        name = str(raw_name or "").strip()
        if not name:
            continue
        base = sanitize_filename(name)
        if base.casefold() in used_stems:
            continue

        candidate_stem = base
        suffix = 2
        while candidate_stem.casefold() in used_stems:
            candidate_stem = f"{base}_{suffix}"
            suffix += 1
        used_stems.add(candidate_stem.casefold())
        details = dict(raw_details) if isinstance(raw_details, dict) else {}
        candidates.append(
            CharacterCardCandidate(
                name=name,
                details=details,
                path=project.canon_dir / "characters" / f"{candidate_stem}.md",
            )
        )
    return candidates


def render_character_card(candidate: CharacterCardCandidate) -> str:
    """Render a conservative draft card from tracked story state."""
    details = candidate.details
    template = str(CANON_ENTRY_TYPES["character"]["template"]).format(
        title=candidate.name
    )
    managed = {
        key: details.get(key)
        for key in ("state", "location", "power_level", "items", "relations")
        if key in details
    }
    rendered = replace_managed_state(template, managed)
    if not managed:
        rendered = rendered.rstrip() + "\n\n- 备注：由故事记忆生成的待完善角色卡。\n"
    return rendered


def create_character_cards(
    project: NovelProject,
    candidates: list[CharacterCardCandidate],
) -> list[Path]:
    """Create confirmed draft cards without overwriting existing files."""
    store = ProjectDataStore(project)
    created: list[Path] = []
    for candidate in candidates:
        try:
            store.write_new_file(candidate.path, render_character_card(candidate))
        except FileExistsError:
            # A file may have appeared after detection; never overwrite it.
            continue
        created.append(candidate.path)
    return created
