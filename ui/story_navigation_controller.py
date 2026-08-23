"""Navigation rules for chapters, canon files, and memory links."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject

from core.project_data import ProjectDataStore


class StoryNavigationController(QObject):
    """Keep story-specific path and route decisions out of MainWindow."""

    def __init__(self, *, project_session, editor, left_panel, show_route, parent=None):
        super().__init__(parent)
        self.project_session = project_session
        self.editor = editor
        self.left_panel = left_panel
        self.show_route = show_route

    @staticmethod
    def route_for_category(category: str) -> str:
        return "writing" if category == "章节" else "canon"

    def select_default_canon(self) -> Path | None:
        project = self.project_session.project
        if project is None or self.editor.current_category() != "章节":
            return None
        store = ProjectDataStore(project)
        paths = [
            project.outline_dir / "main_arc.md",
            *store.list_characters(),
            *store.list_world(),
            *store.list_power(),
            project.canon_dir / "timeline.md",
        ]
        for path in paths:
            if path.exists():
                self.left_panel.select_path(path)
                return path
        return None

    def open_memory_chapter(self, chapter_id: str) -> bool:
        project = self.project_session.project
        if project is None:
            return False
        path = project.chapters_dir / f"{chapter_id}.md"
        if not path.exists():
            return False
        self.show_route("writing")
        self.left_panel.select_path(path)
        return True
