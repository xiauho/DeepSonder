"""Novel project file/folder management.

Layout:
    project.json
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
from copy import deepcopy
from pathlib import Path
from typing import Any

from .models import Chapter, RelatedCanon

DEFAULT_STORY_STATE = {
    "current_chapter": 1,
    "current_location": "",
    "characters": {},
    "foreshadowing": [],
}

DEFAULT_CHAPTER_SUMMARIES = {}


class NovelProject:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.meta = self._read_json(self.root / "project.json")

    # ------------------------------------------------------------------
    # Project creation / loading
    # ------------------------------------------------------------------
    @classmethod
    def create(cls, root: Path, name: str, author: str = "") -> "NovelProject":
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)

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
        (root / "outline" / "main_arc.md").write_text(
            "# 总大纲\n\n- 主线：\n- 支线：\n- 伏笔：\n", encoding="utf-8"
        )
        (root / "outline" / "future_plan.md").write_text(
            "# 后续剧情规划\n\n- 下一阶段主要事件：\n- 必须推进的伏笔：\n- 章节结尾目标：\n",
            encoding="utf-8",
        )
        (root / "canon" / "timeline.md").write_text(
            "# 时间线\n\n| 时间 | 事件 |\n|---|---|\n", encoding="utf-8"
        )
        (root / "memory" / "story_state.json").write_text(
            json.dumps(DEFAULT_STORY_STATE, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (root / "memory" / "chapter_summaries.json").write_text(
            json.dumps(DEFAULT_CHAPTER_SUMMARIES, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        chapter_01 = root / "outline" / "chapters" / "chapter_01.md"
        if not chapter_01.exists():
            chapter_01.write_text(
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
    def chapters_dir(self) -> Path:
        return self.outline_dir / "chapters"

    @property
    def canon_dir(self) -> Path:
        return self.root / "canon"

    @property
    def memory_dir(self) -> Path:
        return self.root / "memory"

    # ------------------------------------------------------------------
    # File listing
    # ------------------------------------------------------------------
    def list_chapters(self) -> list[Path]:
        return self._list_md(self.chapters_dir)

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
            ("大纲", self.outline_dir / "main_arc.md"),
            ("大纲", self.outline_dir / "future_plan.md"),
            ("时间线", self.canon_dir / "timeline.md"),
        ]
        items += [("大纲", p) for p in self.list_chapters()]
        items += [("角色", p) for p in self.list_characters()]
        items += [("世界观", p) for p in self.list_world()]
        items += [("战力", p) for p in self.list_power()]
        return [(cat, p) for cat, p in items if p.exists()]

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------
    def read_file(self, path: Path) -> str:
        return Path(path).read_text(encoding="utf-8")

    def write_file(self, path: Path, text: str) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def chapter_from_path(self, path: Path) -> Chapter | None:
        path = Path(path)
        if path.parent.resolve() != self.chapters_dir.resolve():
            return None
        return self.load_chapter(path.stem)

    def load_chapter(self, chapter_id: str) -> Chapter:
        path = self.chapters_dir / f"{chapter_id}.md"
        raw = self.read_file(path) if path.exists() else ""
        outline, content = self._split_chapter(raw)
        title = path.stem
        if raw.startswith("# "):
            first_line = raw.splitlines()[0]
            title = first_line.lstrip("# ").strip() or path.stem
        return Chapter(
            id=chapter_id,
            path=path,
            title=title,
            outline=outline,
            content=content,
            raw=raw,
        )

    def save_chapter(
        self, chapter_id: str, outline: str, content: str, title: str | None = None
    ) -> None:
        path = self.chapters_dir / f"{chapter_id}.md"
        chapter_title = title or self.load_chapter(chapter_id).title or chapter_id
        text = f"# {chapter_title}\n\n## 大纲\n{outline.strip()}\n\n## 正文\n{content.strip()}\n"
        self.write_file(path, text)

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
    def find_related_canon(self, chapter_id: str) -> RelatedCanon:
        chapter = self.load_chapter(chapter_id)
        haystack = chapter.raw

        characters_parts = []
        for path in self.list_characters():
            name = path.stem
            if name and name in haystack:
                characters_parts.append(f"### {name}\n{self.read_file(path).strip()}")

        world_parts = []
        for path in self.list_world():
            world_parts.append(self.read_file(path).strip())
        power_parts = []
        for path in self.list_power():
            power_parts.append(self.read_file(path).strip())

        timeline = ""
        timeline_path = self.canon_dir / "timeline.md"
        if timeline_path.exists():
            timeline = self.read_file(timeline_path)

        return RelatedCanon(
            world="\n\n".join(world_parts),
            power="\n\n".join(power_parts),
            timeline=timeline,
            characters="\n\n".join(characters_parts),
        )

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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def _list_md(directory: Path) -> list[Path]:
        if not Path(directory).exists():
            return []
        return sorted(Path(directory).glob("*.md"))

    @staticmethod
    def _split_chapter(raw: str) -> tuple[str, str]:
        outline = ""
        content = ""
        in_outline = False
        in_content = False
        has_sections = "## 大纲" in raw or "## 正文" in raw

        for line in raw.splitlines():
            if line.strip().startswith("## 大纲"):
                in_outline, in_content = True, False
                continue
            if line.strip().startswith("## 正文"):
                in_outline, in_content = False, True
                continue
            if line.strip().startswith("# "):
                continue
            if in_outline:
                outline += line + "\n"
            elif in_content:
                content += line + "\n"
        if not has_sections:
            content = "\n".join(
                line for line in raw.splitlines() if not line.startswith("# ")
            )
        return outline.strip(), content.strip()
