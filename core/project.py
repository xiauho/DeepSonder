"""Novel project file/folder management.

Layout:
    project.json
    writing/
        style_guide.md
    outline/
        main_arc.md
        chapters/<chapter_id>.md
    canon/
        characters/<name>.md
        world/<name>.md
        power/<name>.md
        timeline.md
    memory/
        story_state.json
        chapter_summaries.json
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import Chapter, RelatedCanon
from .storage import atomic_write_text

DEFAULT_STORY_STATE = {
    "current_chapter": 1,
    "current_location": "",
    "characters": {},
    "foreshadowing": [],
}

DEFAULT_CHAPTER_SUMMARIES = {}
DEFAULT_STYLE_GUIDE = (
    "# 写作风格指南\n\n"
    "<!-- 填写本项目长期使用的表达偏好。未填写时，AI 扩写不会应用额外风格约束。 -->\n\n"
    "## 总体气质\n\n"
    "<!-- 例如：冷峻克制、轻快幽默、细腻舒缓。请使用特征描述，不建议填写作者姓名。 -->\n\n"
    "## 叙事视角\n\n"
    "<!-- 例如：第三人称限知，主要跟随主角感知。 -->\n\n"
    "## 句式与节奏\n\n"
    "<!-- 例如：动作场景短句为主，日常场景适度放缓。 -->\n\n"
    "## 对话风格\n\n"
    "<!-- 例如：对白简短，通过冲突和试探传递信息。 -->\n\n"
    "## 描写偏好\n\n"
    "<!-- 例如：优先动作、空间和感官细节，减少抽象总结。 -->\n\n"
    "## 避免事项\n\n"
    "<!-- 例如：避免连续排比、过度比喻、总结式升华和网络流行语。 -->\n\n"
    "## 作者自有样例\n\n"
    "<!-- 可粘贴少量由你拥有权利、能够代表本书风格的原创段落。 -->\n"
)
DEFAULT_TIMELINE = "# 时间线\n\n| 时间 | 事件 |\n|---|---|\n"
CORE_POWER_FILENAME = "_核心规则.md"
DEFAULT_CORE_POWER_RULES = (
    "# 核心规则\n\n"
    "## 全书不可违背的规则\n\n"
    "## 基础限制\n\n"
    "## 全局例外\n"
)
SYSTEM_REGISTRY_FILENAME = "system_registry.json"
DEFAULT_ABILITY_SYSTEM = (
    "# 能力体系设定\n\n"
    "## 体系定位\n\n"
    "## 等级与阶段\n\n"
    "## 能力规则\n\n"
    "## 使用限制\n\n"
    "## 消耗与代价\n\n"
    "## 晋升与突破\n\n"
    "## 特殊例外\n"
)
DEFAULT_SPACE_SYSTEM = (
    "# 空间体系设定\n\n"
    "## 空间层级\n\n"
    "## 空间之间的关系\n\n"
    "## 进入与离开条件\n\n"
    "## 时间流速差异\n\n"
    "## 空间边界与限制\n\n"
    "## 空间资源\n\n"
    "## 与主世界的关系\n"
)


def chapter_number_from_id(chapter_id: str) -> int | None:
    """Chapter ordinal for ids following the app's ``chapter_07`` naming.

    Returns ``None`` for imported or custom ids so callers fall back to their
    own default instead of trusting digits that are part of a title.
    """
    match = re.fullmatch(r"chapter[_-]?(\d+)", str(chapter_id or "").strip(), re.IGNORECASE)
    return int(match.group(1)) if match else None


def chapter_sort_key(value: str | Path) -> tuple[int, int, str]:
    """Return one stable natural-order key for chapter ids and paths.

    Canonical ids such as ``chapter_2`` sort before custom/imported ids, and
    numeric chapter ids use their numeric value instead of lexical order.
    """
    chapter_id = value.stem if isinstance(value, Path) else str(value)
    number = chapter_number_from_id(chapter_id)
    if number is not None:
        return 0, number, chapter_id.casefold()
    return 1, 0, chapter_id.casefold()


class NovelProject:
    def __init__(self, root: Path):
        # Keep every project-owned path absolute.  UI actions may be emitted
        # long after a project was opened, and relative paths would otherwise
        # be resolved against whichever working directory launched the app.
        self.root = Path(root).expanduser().resolve()
        self.meta = self._read_json(self.root / "project.json")
        self._related_canon_cache: dict[
            tuple[object, ...], tuple[tuple[tuple[str, int, int], ...], RelatedCanon]
        ] = {}

    # ------------------------------------------------------------------
    # Project creation / loading
    # ------------------------------------------------------------------
    @classmethod
    def create(cls, root: Path, name: str, author: str = "") -> "NovelProject":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)

        (root / "writing").mkdir(parents=True, exist_ok=True)
        (root / "outline" / "chapters").mkdir(parents=True, exist_ok=True)
        (root / "canon" / "characters").mkdir(parents=True, exist_ok=True)
        (root / "canon" / "world").mkdir(parents=True, exist_ok=True)
        (root / "canon" / "power").mkdir(parents=True, exist_ok=True)
        (root / "memory").mkdir(parents=True, exist_ok=True)

        meta = {
            "id": root.name,
            "name": name or root.name,
            "author": author,
            "created_at": "",
            "updated_at": "",
        }
        cls._write_json(root / "project.json", meta)

        # Skeleton files
        atomic_write_text(
            root / "writing" / "style_guide.md",
            DEFAULT_STYLE_GUIDE,
            encoding="utf-8",
        )
        atomic_write_text(
            root / "outline" / "main_arc.md",
            "# 总大纲\n\n- 主线：\n- 支线：\n- 伏笔：\n", encoding="utf-8"
        )
        atomic_write_text(
            root / "outline" / "future_plan.md",
            "# 后续剧情规划\n\n- 下一阶段主要事件：\n- 必须推进的伏笔：\n- 章节结尾目标：\n",
            encoding="utf-8",
        )
        atomic_write_text(
            root / "canon" / "timeline.md",
            DEFAULT_TIMELINE, encoding="utf-8"
        )
        atomic_write_text(
            root / "canon" / "power" / CORE_POWER_FILENAME,
            DEFAULT_CORE_POWER_RULES,
            encoding="utf-8",
        )
        ability_path = root / "canon" / "power" / "能力体系设定.md"
        space_path = root / "canon" / "power" / "空间体系设定.md"
        atomic_write_text(ability_path, DEFAULT_ABILITY_SYSTEM, encoding="utf-8")
        atomic_write_text(space_path, DEFAULT_SPACE_SYSTEM, encoding="utf-8")
        atomic_write_text(
            root / "canon" / SYSTEM_REGISTRY_FILENAME,
            json.dumps(
                {
                    "version": 1,
                    "entries": {
                        "canon/power/能力体系设定.md": {
                            "type": "ability",
                            "importance": "non_core",
                        },
                        "canon/power/空间体系设定.md": {
                            "type": "space",
                            "importance": "non_core",
                        },
                    },
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        atomic_write_text(
            root / "memory" / "story_state.json",
            json.dumps(DEFAULT_STORY_STATE, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        atomic_write_text(
            root / "memory" / "chapter_summaries.json",
            json.dumps(DEFAULT_CHAPTER_SUMMARIES, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        atomic_write_text(
            root / "memory" / "foreshadowing.json",
            json.dumps({"version": 1, "items": []}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        chapter_01 = root / "outline" / "chapters" / "chapter_01.md"
        if not chapter_01.exists():
            atomic_write_text(
                chapter_01,
                "# 第一章 初始\n\n## 大纲\n- 在这里写本章剧情目标\n\n## 正文\n在这里开始写作。\n",
                encoding="utf-8",
            )

        return cls(root)

    @classmethod
    def is_project(cls, root: Path) -> bool:
        return (Path(root) / "project.json").is_file()

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.meta.get("name", self.root.name)

    @property
    def outline_dir(self) -> Path:
        return self.root / "outline"

    @property
    def writing_dir(self) -> Path:
        return self.root / "writing"

    @property
    def style_guide_path(self) -> Path:
        return self.writing_dir / "style_guide.md"

    @property
    def chapters_dir(self) -> Path:
        return self.outline_dir / "chapters"

    @property
    def canon_dir(self) -> Path:
        return self.root / "canon"

    @property
    def core_power_path(self) -> Path:
        return self.canon_dir / "power" / CORE_POWER_FILENAME

    @property
    def system_registry_path(self) -> Path:
        return self.canon_dir / SYSTEM_REGISTRY_FILENAME

    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    # ------------------------------------------------------------------
    # File listing
    # ------------------------------------------------------------------
    def list_chapters(self) -> list[Path]:
        return sorted(self._list_md(self.chapters_dir), key=chapter_sort_key)

    def list_characters(self) -> list[Path]:
        return self._list_md(self.canon_dir / "characters")

    def list_world(self) -> list[Path]:
        return self._list_md(self.canon_dir / "world")

    def list_power(self) -> list[Path]:
        return self._list_md(self.canon_dir / "power")

    def list_outline(self) -> list[Path]:
        files = [
            self.outline_dir / "main_arc.md",
            self.outline_dir / "future_plan.md",
        ]
        files += self.list_chapters()
        return [p for p in files if p.exists()]

    def load_optional_text(self, path: Path, default: str = "") -> str:
        """Read an optional planning/context file without failing a task."""
        path = Path(path)
        if not path.exists():
            return default
        try:
            return self.read_file(path).strip()
        except (OSError, UnicodeError):
            return default

    def load_main_arc(self) -> str:
        return self.load_optional_text(self.outline_dir / "main_arc.md")

    def load_future_plan(self) -> str:
        return self.load_optional_text(self.outline_dir / "future_plan.md")

    def list_all_editable_files(self) -> list[tuple[str, Path]]:
        """Return (category, path) pairs for the left navigation tree."""
        items: list[tuple[str, Path]] = [
            ("写作风格", self.style_guide_path),
            ("大纲", self.outline_dir / "main_arc.md"),
            ("大纲", self.outline_dir / "future_plan.md"),
            ("时间线", self.canon_dir / "timeline.md"),
        ]
        items += [("大纲", p) for p in self.list_chapters()]
        items += [("角色", p) for p in self.list_characters()]
        items += [("世界观", p) for p in self.list_world()]
        items += [("体系设定", p) for p in self.list_power()]
        return [(cat, p) for cat, p in items if p.exists()]

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------
    def read_file(self, path: Path) -> str:
        return Path(path).read_text(encoding="utf-8")

    def write_file(self, path: Path, text: str) -> None:
        path = Path(path)
        atomic_write_text(path, text)

    def chapter_from_path(self, path: Path) -> Chapter | None:
        path = Path(path)
        if path.parent.resolve() != self.chapters_dir.resolve():
            return None
        return self.load_chapter(path.stem)

    def load_chapter(self, chapter_id: str) -> Chapter:
        path = self.chapters_dir / f"{chapter_id}.md"
        raw = self.read_file(path) if path.exists() else ""
        outline, plot_brief, content, extra_sections = self._parse_chapter(raw)
        title = path.stem
        if raw.startswith("# "):
            first_line = raw.splitlines()[0]
            title = first_line.lstrip("# ").strip() or path.stem
        return Chapter(
            id=chapter_id,
            path=path,
            title=title,
            outline=outline,
            plot_brief=plot_brief,
            content=content,
            raw=raw,
            extra_sections=extra_sections,
        )

    def save_chapter(
        self,
        chapter_id: str,
        outline: str,
        content: str,
        title: str | None = None,
        *,
        plot_brief: str | None = None,
        extra_sections: list[tuple[str, str]] | None = None,
    ) -> None:
        path = self.chapters_dir / f"{chapter_id}.md"
        existing = self.load_chapter(chapter_id) if path.exists() else None
        chapter_title = title or (existing.title if existing else "") or chapter_id
        if plot_brief is None:
            plot_brief = existing.plot_brief if existing else ""
        if extra_sections is None:
            extra_sections = existing.extra_sections if existing else []
        text = self.serialize_chapter(
            chapter_title,
            outline,
            plot_brief,
            content,
            extra_sections,
        )
        self.write_file(path, text)

    @staticmethod
    def serialize_chapter(
        title: str,
        outline: str = "",
        plot_brief: str = "",
        content: str = "",
        extra_sections: list[tuple[str, str]] | None = None,
    ) -> str:
        """Serialize the canonical chapter sections without dropping extras."""
        blocks = [f"# {str(title).strip() or '未命名章节'}"]
        for heading, body in (
            ("大纲", outline),
            ("剧情简写", plot_brief),
            ("正文", content),
        ):
            blocks.append(f"## {heading}\n{str(body or '').strip()}")
        for heading, body in extra_sections or []:
            heading = str(heading).strip()
            if heading and heading not in {"大纲", "剧情简写", "正文"}:
                blocks.append(f"## {heading}\n{str(body or '').strip()}")
        return "\n\n".join(blocks).rstrip() + "\n"

    @staticmethod
    def replace_chapter_body(raw: str, content: str) -> str:
        """Replace only the正文 section while preserving later custom sections.

        Chapters may contain application-defined ``##`` sections after the正文
        section.  AI expansion must not silently delete those sections.
        """
        lines = str(raw or "").splitlines()
        body_index = next(
            (
                index
                for index, line in enumerate(lines)
                if re.fullmatch(r"\s*##\s+正文\s*", line)
            ),
            None,
        )
        body_text = str(content or "").strip()
        if body_index is None:
            base = "\n".join(lines).rstrip()
            return f"{base}\n\n## 正文\n{body_text}\n"

        next_section = len(lines)
        for index in range(body_index + 1, len(lines)):
            if re.match(r"^\s*##\s+", lines[index]):
                next_section = index
                break

        prefix = "\n".join(lines[: body_index + 1]).rstrip()
        suffix = "\n".join(lines[next_section:]).strip()
        result = f"{prefix}\n{body_text}"
        if suffix:
            result += f"\n\n{suffix}"
        return result.rstrip() + "\n"

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------
    def load_story_state(self) -> dict:
        state = self._read_json(
            self.memory_dir / "story_state.json", DEFAULT_STORY_STATE
        )
        return state if isinstance(state, dict) else dict(DEFAULT_STORY_STATE)

    def save_story_state(self, state: dict) -> None:
        self._write_json(self.memory_dir / "story_state.json", state)

    def load_chapter_summaries(self) -> dict:
        summaries = self._read_json(
            self.memory_dir / "chapter_summaries.json", DEFAULT_CHAPTER_SUMMARIES
        )
        return summaries if isinstance(summaries, dict) else {}

    def save_chapter_summaries(self, summaries: dict) -> None:
        self._write_json(self.memory_dir / "chapter_summaries.json", summaries)

    # ------------------------------------------------------------------
    # Related canon lookup (MVP keyword/tag based)
    # ------------------------------------------------------------------
    def find_related_canon(
        self,
        chapter_id: str,
        *,
        character_query: str | None = None,
        include_world: bool = True,
        include_power: bool = True,
        include_timeline: bool = True,
        selected_power: list[str | Path] | tuple[str | Path, ...] | None = None,
        core_power_paths: list[str | Path] | tuple[str | Path, ...] | None = None,
    ) -> RelatedCanon:
        """Load canon relevant to one task without forcing every canon file in.

        The default remains backward-compatible and loads the same project-wide
        canon as before.  Task-specific context builders can narrow the query
        and omit expensive low-signal sections such as the full world/power
        directories.
        """
        cache_key = (
            str(chapter_id),
            character_query,
            bool(include_world),
            bool(include_power),
            bool(include_timeline),
            tuple(self._selected_power_keys(selected_power)),
            tuple(self._selected_power_keys(core_power_paths)),
        )
        signature = self._related_canon_signature(
            chapter_id,
            include_world=include_world,
            include_power=include_power,
            include_timeline=include_timeline,
            selected_power=selected_power,
            core_power_paths=core_power_paths,
        )
        cached = self._related_canon_cache.get(cache_key)
        if cached is not None and cached[0] == signature:
            return cached[1]

        chapter = self.load_chapter(chapter_id)
        haystack = character_query if character_query is not None else chapter.raw

        characters_parts = []
        for path in self.list_characters():
            name = path.stem
            if name and name in haystack:
                characters_parts.append(f"### {name}\n{self.read_file(path).strip()}")

        world_parts = []
        if include_world:
            for path in self.list_world():
                world_parts.append(self.read_file(path).strip())
        core_power = ""
        core_system_parts = []
        selected_power_parts = []
        power_parts = []
        if include_power:
            core_path = self.core_power_path
            if core_path.exists():
                core_power = self.read_file(core_path).strip()
            selected_paths = self._selected_power_paths(selected_power)
            core_paths = self._selected_power_paths(core_power_paths)
            selected_keys = {str(path).casefold() for path in selected_paths}
            core_keys = {str(path).casefold() for path in core_paths}
            for path in core_paths:
                core_system_parts.append(
                    f"### {path.stem}\n{self.read_file(path).strip()}"
                )
            for path in selected_paths:
                if str(path).casefold() in core_keys:
                    continue
                selected_power_parts.append(
                    f"### {path.stem}\n{self.read_file(path).strip()}"
                )
            for path in self.list_power():
                if (
                    path.resolve() == core_path.resolve()
                    or str(path.resolve()).casefold() in core_keys
                    or str(path.resolve()).casefold() in selected_keys
                ):
                    continue
                power_parts.append(f"### {path.stem}\n{self.read_file(path).strip()}")

        timeline = ""
        timeline_path = self.canon_dir / "timeline.md"
        if include_timeline and timeline_path.exists():
            timeline = self.read_file(timeline_path)

        related = RelatedCanon(
            world="\n\n".join(world_parts),
            power="\n\n".join(power_parts),
            timeline=timeline,
            characters="\n\n".join(characters_parts),
            core_power=core_power,
            core_systems="\n\n".join(core_system_parts),
            selected_power="\n\n".join(selected_power_parts),
        )
        self._related_canon_cache[cache_key] = (signature, related)
        return related

    def _related_canon_signature(
        self,
        chapter_id: str,
        *,
        include_world: bool = True,
        include_power: bool = True,
        include_timeline: bool = True,
        selected_power: list[str | Path] | tuple[str | Path, ...] | None = None,
        core_power_paths: list[str | Path] | tuple[str | Path, ...] | None = None,
    ) -> tuple[tuple[str, int, int], ...]:
        paths = [
            self.chapters_dir / f"{chapter_id}.md",
            *self.list_characters(),
        ]
        if include_timeline:
            paths.append(self.canon_dir / "timeline.md")
        if include_world:
            paths.extend(self.list_world())
        if include_power:
            paths.extend(self.list_power())
            paths.append(self.system_registry_path)
        paths.extend(self._selected_power_paths(selected_power))
        paths.extend(self._selected_power_paths(core_power_paths))
        signature: list[tuple[str, int, int]] = []
        for path in paths:
            path = Path(path)
            key = str(path.resolve()).casefold()
            try:
                stat = path.stat()
            except OSError:
                signature.append((key, -1, -1))
            else:
                signature.append((key, stat.st_mtime_ns, stat.st_size))
        return tuple(signature)

    def _selected_power_paths(
        self,
        selected_power: list[str | Path] | tuple[str | Path, ...] | None,
    ) -> list[Path]:
        power_dir = (self.canon_dir / "power").resolve()
        result: list[Path] = []
        for raw_path in selected_power or ():
            path = Path(raw_path)
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if resolved.parent != power_dir or resolved == self.core_power_path.resolve():
                continue
            if resolved.is_file() and resolved.suffix.lower() == ".md":
                result.append(resolved)
        return sorted(set(result), key=lambda item: item.name.casefold())

    def _selected_power_keys(
        self,
        selected_power: list[str | Path] | tuple[str | Path, ...] | None,
    ) -> list[str]:
        return [str(path).casefold() for path in self._selected_power_paths(selected_power)]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _read_json(self, path: Path, default: Any = None) -> Any:
        if not Path(path).exists():
            return self._copy_default(default)
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return self._copy_default(default)

    @staticmethod
    def _copy_default(default: Any) -> Any:
        if default is None:
            return {}
        return deepcopy(default)

    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        atomic_write_text(
            path,
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _list_md(directory: Path) -> list[Path]:
        if not Path(directory).exists():
            return []
        return sorted(Path(directory).glob("*.md"))

    @staticmethod
    def _parse_chapter(raw: str) -> tuple[str, str, str, list[tuple[str, str]]]:
        """Parse standard chapter sections while preserving unknown sections."""
        sections: dict[str, list[str]] = {}
        section_order: list[str] = []
        current: str | None = None
        has_level_two_section = False

        for line in raw.splitlines():
            heading = re.match(r"^##\s+(.+?)\s*$", line.strip())
            if heading:
                current = heading.group(1).strip()
                has_level_two_section = True
                if current not in sections:
                    sections[current] = []
                    section_order.append(current)
                continue
            if line.startswith("# "):
                continue
            if current is not None:
                sections.setdefault(current, []).append(line)
            elif not has_level_two_section:
                sections.setdefault("正文", []).append(line)

        def body(name: str) -> str:
            return "\n".join(sections.get(name, [])).strip()

        extras = [
            (name, body(name))
            for name in section_order
            if name not in {"大纲", "剧情简写", "正文"} and body(name)
        ]
        return body("大纲"), body("剧情简写"), body("正文"), extras

    @staticmethod
    def _split_chapter(raw: str) -> tuple[str, str]:
        """Backward-compatible outline/content view of a chapter document."""
        outline, _plot_brief, content, _extra_sections = NovelProject._parse_chapter(raw)
        return outline, content
