"""Project creation, loading, restoration, and recent-project state."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject

from core.config import save_config
from core.project import NovelProject


class ProjectSwitchCancelled(Exception):
    """Raised when the current document could not be safely saved."""


class ProjectLifecycleController(QObject):
    """Keep project lifecycle rules separate from window dialogs and routing."""

    def __init__(
        self,
        *,
        project_session,
        config: dict,
        is_task_running: Callable[[], bool],
        save_if_dirty: Callable[[], bool],
        persist_config: Callable[[dict], None] = save_config,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_session = project_session
        self.config = config
        self.is_task_running = is_task_running
        self.save_if_dirty = save_if_dirty
        self.persist_config = persist_config

    def set_config(self, config: dict) -> None:
        self.config = config

    def load(self, path: Path):
        """Validate, save, load, activate, and remember a project."""
        path = Path(path)
        if self.is_task_running():
            raise RuntimeError("AI 任务仍在进行，请等待当前任务完成后再切换项目。")
        if not self.is_project_path(path):
            raise ValueError(f"该目录不是有效的 Novalist 创作项目：\n{path}")
        if not self.save_if_dirty():
            raise ProjectSwitchCancelled()
        project = self.project_session.load(path)
        self.remember_project(project.root)
        return project

    def create_and_load(self, parent_dir: Path, name: str):
        root = Path(parent_dir) / self.safe_name(name)
        if root.exists():
            raise FileExistsError(root)
        NovelProject.create(root, name=name.strip())
        return self.load(root)

    def restore_last(self):
        path = self.safe_project_path(self.config.get("last_project"))
        return self.load(path) if path is not None else None

    def remember_project(self, path: Path) -> None:
        resolved = str(Path(path).resolve())
        recent = [
            str(item)
            for item in self.config.get("recent_projects", [])
            if str(item) != resolved
        ]
        self.config["recent_projects"] = [resolved] + recent[:7]
        self.config["last_project"] = resolved
        self.persist_config(self.config)

    def recent_projects(self) -> list[Path]:
        """Return valid, deduplicated recent projects and clean stale entries."""
        raw_paths = self.config.get("recent_projects", [])
        if not isinstance(raw_paths, list):
            raw_paths = []
        paths: list[Path] = []
        seen: set[str] = set()
        for raw_path in raw_paths:
            path = self.safe_project_path(raw_path)
            if path is None:
                continue
            key = str(path).casefold()
            if key in seen:
                continue
            seen.add(key)
            paths.append(path)

        cleaned_paths = [str(path) for path in paths]
        if cleaned_paths != [str(path) for path in raw_paths]:
            self.config["recent_projects"] = cleaned_paths
            if self.safe_project_path(self.config.get("last_project")) is None:
                self.config["last_project"] = ""
            self.persist_config(self.config)
        return paths

    @staticmethod
    def safe_name(value: str) -> str:
        forbidden = '<>:"/\\|?*'
        cleaned = "".join("_" if char in forbidden else char for char in value).strip(" .")
        return cleaned or "untitled"

    @classmethod
    def safe_project_path(cls, value: object) -> Path | None:
        if not value:
            return None
        try:
            path = Path(str(value)).expanduser()
            return path.resolve() if cls.is_project_path(path) else None
        except (OSError, TypeError, ValueError):
            return None

    @staticmethod
    def is_project_path(path: Path) -> bool:
        try:
            return path.is_dir() and NovelProject.is_project(path)
        except OSError:
            return False
