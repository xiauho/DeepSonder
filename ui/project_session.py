"""Current-project state shared by the desktop UI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Signal

from core.project import NovelProject
from core.project_data import ProjectDataStore


class ProjectSession(QObject):
    """Own the active project and publish changes that views can observe."""

    project_changed = Signal(object)
    data_changed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: NovelProject | None = None
        self._data_store: ProjectDataStore | None = None

    @property
    def project(self) -> NovelProject | None:
        return self._project

    @property
    def data_store(self) -> ProjectDataStore | None:
        """Cached data facade for the active project."""
        return self._data_store

    def require_data_store(self) -> ProjectDataStore:
        if self._data_store is None:
            raise RuntimeError("当前没有打开的项目。")
        return self._data_store

    def load(self, path: Path) -> NovelProject:
        """Load and activate one valid project directory."""
        path = Path(path)
        if not NovelProject.is_project(path):
            raise ValueError(f"该目录不是有效的 Novalist 创作项目：\n{path}")
        project = NovelProject(path)
        self.set_project(project)
        return project

    def set_project(self, project: NovelProject | None) -> None:
        if project is not None and not isinstance(project, NovelProject):
            raise TypeError("project 必须是 NovelProject 或 None。")
        self._project = project
        self._data_store = ProjectDataStore(project) if project is not None else None
        self.project_changed.emit(project)

    def notify_data_changed(self) -> None:
        """Tell dependent views that files in the active project changed."""
        if self._project is not None:
            self.data_changed.emit(self._project)

    def clear(self) -> None:
        self.set_project(None)
