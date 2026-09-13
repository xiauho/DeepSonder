"""Project creation, loading, restoration, and recent-project state."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PySide6.QtCore import QObject

from application.project_service import ProjectService
from core.config import save_config


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
        project_service: ProjectService | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.project_session = project_session
        self.config = config
        self.is_task_running = is_task_running
        self.save_if_dirty = save_if_dirty
        self.persist_config = persist_config
        self.project_service = (
            project_service
            or getattr(project_session, "project_service", None)
            or ProjectService()
        )

    def set_config(self, config: dict) -> None:
        self.config = config

    def load(self, path: Path):
        """Validate, save, load, activate, and remember a project."""
        path = Path(path)
        if self.is_task_running():
            raise RuntimeError("AI 任务仍在进行，请等待当前任务完成后再切换项目。")
        path = self.project_service.require_project_path(path)
        if not self.save_if_dirty():
            raise ProjectSwitchCancelled()
        project = self.project_session.load(path)
        self.remember_project(project.root)
        return project

    def create_and_load(self, parent_dir: Path, name: str):
        if self.is_task_running():
            raise RuntimeError("AI 任务仍在进行，请等待当前任务完成后再切换项目。")
        if not self.save_if_dirty():
            raise ProjectSwitchCancelled()
        opened = self.project_service.create_project(parent_dir, name)
        project = self.project_session.activate_opened(opened)
        self.remember_project(project.root)
        return project

    def restore_last(self):
        path = self.project_service.safe_project_path(self.config.get("last_project"))
        return self.load(path) if path is not None else None

    def remember_project(self, path: Path) -> None:
        self.project_service.remember_project(self.config, path)
        self.persist_config(self.config)

    def recent_projects(self) -> list[Path]:
        """Return valid, deduplicated recent projects and clean stale entries."""
        result = self.project_service.recent_projects(self.config)
        if result.changed:
            self.persist_config(self.config)
        return list(result.paths)

    @staticmethod
    def safe_name(value: str) -> str:
        return ProjectService.safe_name(value)

    @classmethod
    def safe_project_path(cls, value: object) -> Path | None:
        return ProjectService().safe_project_path(value)

    @staticmethod
    def is_project_path(path: Path) -> bool:
        return ProjectService.is_project_path(path)
