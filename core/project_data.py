"""Unified project data access for UI and application services."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .project import NovelProject


class ProjectDataStore:
    """Facade around project persistence and common read-only projections.

    ``NovelProject`` remains the compatible low-level model. New UI and service
    code should use this facade so file layout, fallbacks and multi-file writes
    stay in one place.
    """

    def __init__(self, project: NovelProject):
        self.project = project

    @property
    def name(self) -> str:
        return self.project.name

    @property
    def root(self) -> Path:
        return self.project.root

    def list_chapters(self) -> list[Path]:
        return self.project.list_chapters()

    def list_characters(self) -> list[Path]:
        return self.project.list_characters()

    def list_world(self) -> list[Path]:
        return self.project.list_world()

    def list_power(self) -> list[Path]:
        return self.project.list_power()

    def load_chapter(self, chapter_id: str):
        return self.project.load_chapter(chapter_id)

    def find_related_canon(self, chapter_id: str):
        return self.project.find_related_canon(chapter_id)

    def load_story_state(self) -> dict:
        return self.project.load_story_state()

    def load_chapter_summaries(self) -> dict:
        return self.project.load_chapter_summaries()

    def load_main_arc(self) -> str:
        return self.project.load_main_arc()

    def load_future_plan(self) -> str:
        return self.project.load_future_plan()

    def save_story_state(self, state: dict) -> None:
        self.project.save_story_state(state)

    def save_chapter_summaries(self, summaries: dict) -> None:
        self.project.save_chapter_summaries(summaries)

    def save_chapter_summary(self, chapter_id: str, summary: str) -> None:
        summaries = self.load_chapter_summaries()
        summaries[chapter_id] = str(summary or "").strip()
        self.save_chapter_summaries(summaries)

    def write_new_file(self, path: Path, text: str) -> None:
        """Create one new project file without allowing accidental overwrite."""
        path = Path(path)
        root = self.root.resolve()
        target = path.resolve()
        if root not in target.parents:
            raise ValueError("目标文件必须位于当前项目目录内。")
        if path.exists():
            raise FileExistsError(path)
        self.project.write_file(path, text)

    def commit_memory_update(
        self,
        chapter_id: str,
        summary: str,
        state: dict,
    ) -> None:
        """Persist summary and state together with best-effort rollback."""
        old_summaries = deepcopy(self.load_chapter_summaries())
        old_state = deepcopy(self.load_story_state())
        new_summaries = deepcopy(old_summaries)
        new_summaries[chapter_id] = str(summary or "").strip()
        try:
            self.save_chapter_summaries(new_summaries)
            self.save_story_state(state)
        except Exception:
            try:
                self.save_chapter_summaries(old_summaries)
                self.save_story_state(old_state)
            except Exception:
                # Preserve the original failure; callers still receive a clear
                # error while the next load can surface any rollback issue.
                pass
            raise

    def read_text(self, path: Path, *, default: str = "") -> str:
        try:
            return self.project.read_file(Path(path))
        except (OSError, UnicodeError):
            return default

    def chapter_display_name(self, path: Path) -> str:
        path = Path(path)
        first_line = self.read_text(path).splitlines()
        if first_line and first_line[0].startswith("# "):
            return first_line[0][2:].strip() or path.stem
        return path.stem

    def total_word_count(self, counter) -> int:
        total = 0
        for path in self.list_chapters():
            total += int(counter(self.read_text(path)))
        return total

    def total_chinese_character_count(self) -> int:
        import re

        return sum(
            len(re.findall(r"[\u3400-\u9fff]", self.read_text(path)))
            for path in self.list_chapters()
        )
