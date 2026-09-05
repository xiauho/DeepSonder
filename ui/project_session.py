"""Current-project state shared by the desktop UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import QObject, Signal

from core.project import NovelProject
from core.project_data import ProjectDataStore
from core.project_migrations import ProjectMigrationResult, migrate_project


@dataclass(frozen=True)
class ProjectChange:
    """A narrow description of files changed in the active project."""

    paths: tuple[str, ...] = ()
    kind: str = "project"
    full_refresh: bool = False

    @property
    def path_set(self) -> set[str]:
        return {str(Path(path).resolve()).casefold() for path in self.paths}


class ProjectSession(QObject):
    """Own the active project and publish changes that views can observe."""

    project_changed = Signal(object)
    data_change_detail = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: NovelProject | None = None
        self._data_store: ProjectDataStore | None = None
        self._last_migration_result: ProjectMigrationResult | None = None

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

    @property
    def last_migration_result(self) -> ProjectMigrationResult | None:
        return self._last_migration_result

    def load(self, path: Path) -> NovelProject:
        """Load and activate one valid project directory."""
        path = Path(path)
        if not NovelProject.is_project(path):
            raise ValueError(f"该目录不是有效的 Novalist 创作项目：\n{path}")
        self._last_migration_result = migrate_project(path)
        project = NovelProject(path)
        self.set_project(project)
        return project

    def set_project(self, project: NovelProject | None) -> None:
        if project is not None and not isinstance(project, NovelProject):
            raise TypeError("project 必须是 NovelProject 或 None。")
        self._project = project
        self._data_store = ProjectDataStore(project) if project is not None else None
        self.project_changed.emit(project)

    def notify_data_changed(
        self,
        paths: Iterable[Path | str] | None = None,
        *,
        kind: str = "project",
    ) -> None:
        """Tell dependent views exactly which project data changed."""
        if self._project is not None:
            change = ProjectChange(
                paths=tuple(str(Path(path)) for path in (paths or ())),
                kind=str(kind or "project"),
                full_refresh=paths is None,
            )
            self.data_change_detail.emit(self._project, change)

    def clear(self) -> None:
        self.set_project(None)
