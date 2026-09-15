"""Simple data containers used by the novel writing tool."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .context_selection import CanonSelectionStat


@dataclass
class Chapter:
    id: str
    path: Path
    title: str
    outline: str = ""
    plot_brief: str = ""
    content: str = ""
    raw: str = ""
    extra_sections: list[tuple[str, str]] = field(default_factory=list)

    @property
    def filename(self) -> str:
        return self.path.name


@dataclass
class RelatedCanon:
    world: str = ""
    power: str = ""
    timeline: str = ""
    characters: str = ""
    core_power: str = ""
    core_systems: str = ""
    selected_power: str = ""
    selection: tuple[CanonSelectionStat, ...] = ()

    def to_block(self) -> str:
        parts = []
        if self.world:
            parts.append(f"【世界观摘要】\n{self.world.strip()}")
        if self.core_power:
            parts.append(f"【常驻核心规则】\n{self.core_power.strip()}")
        if self.core_systems:
            parts.append(f"【核心体系设定】\n{self.core_systems.strip()}")
        if self.selected_power:
            parts.append(f"【本次重点体系设定】\n{self.selected_power.strip()}")
        if self.power:
            parts.append(
                f"【其他体系设定·低优先级背景】\n{self.power.strip()}"
            )
        if self.timeline:
            parts.append(f"【时间线摘要】\n{self.timeline.strip()}")
        if self.characters:
            parts.append(f"【相关角色卡】\n{self.characters.strip()}")
        return "\n\n".join(parts)


@dataclass
class StoryState:
    current_chapter: int = 1
    current_location: str = ""
    characters: dict = field(default_factory=dict)
    foreshadowing: list = field(default_factory=list)

    @classmethod
    def default(cls) -> "StoryState":
        return cls()
