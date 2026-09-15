"""Project lifecycle rules without desktop-framework dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.project import NovelProject
from core.project_data import ProjectDataStore, sanitize_filename
from core.project_migrations import ProjectMigrationResult, migrate_project


@dataclass(frozen=True)
class OpenedProject:
    """One validated project and the facades created for an active session."""

    project: NovelProject
    data_store: ProjectDataStore
    migration: ProjectMigrationResult


@dataclass(frozen=True)
class RecentProjects:
    """Normalized recent paths plus whether the caller should persist config."""

    paths: tuple[Path, ...]
    changed: bool


class ProjectService:
    """Open, create, and normalize project state for any desktop frontend."""

    RECENT_PROJECT_LIMIT = 8

    def open_project(self, path: Path | str) -> OpenedProject:
        project_path = self.require_project_path(path)
        migration = migrate_project(project_path)
        project = NovelProject(project_path)
        return OpenedProject(project, ProjectDataStore(project), migration)

    def create_project(
        self,
        parent_dir: Path | str,
        name: str,
        *,
        author: str = "",
    ) -> OpenedProject:
        parent = Path(parent_dir).expanduser().resolve()
        if not parent.is_dir():
            raise NotADirectoryError(parent)
        display_name = str(name or "").strip()
        root = parent / self.safe_name(display_name)
        if root.exists():
            raise FileExistsError(root)
        NovelProject.create(root, name=display_name, author=str(author or "").strip())
        return self.open_project(root)

    def require_project_path(self, path: Path | str) -> Path:
        candidate = Path(path).expanduser()
        try:
            resolved = candidate.resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"项目路径无效：{candidate}") from exc
        if not self.is_project_path(resolved):
            raise ValueError(f"该目录不是有效的 Novalist 创作项目：\n{resolved}")
        return resolved

    def safe_project_path(self, value: object) -> Path | None:
        if not value:
            return None
        try:
            path = Path(str(value)).expanduser().resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        return path if self.is_project_path(path) else None

    @staticmethod
    def is_project_path(path: Path | str) -> bool:
        try:
            candidate = Path(path)
            return candidate.is_dir() and NovelProject.is_project(candidate)
        except (OSError, TypeError, ValueError):
            return False

    @staticmethod
    def safe_name(value: str) -> str:
        return sanitize_filename(value)

    def remember_project(self, config: dict, path: Path | str) -> bool:
        """Mutate config with one active project and report whether it changed."""
        resolved = str(self.require_project_path(path))
        old_recent = config.get("recent_projects", [])
        raw_recent = old_recent if isinstance(old_recent, list) else []
        resolved_key = resolved.casefold()
        recent = [
            str(item)
            for item in raw_recent
            if str(item).casefold() != resolved_key
        ]
        normalized = [resolved] + recent[: self.RECENT_PROJECT_LIMIT - 1]
        changed = (
            normalized != raw_recent
            or config.get("last_project") != resolved
        )
        config["recent_projects"] = normalized
        config["last_project"] = resolved
        return changed

    def recent_projects(self, config: dict) -> RecentProjects:
        """Return valid deduplicated projects and clean stale config entries."""
        raw_value = config.get("recent_projects", [])
        raw_paths = raw_value if isinstance(raw_value, list) else []
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
            if len(paths) >= self.RECENT_PROJECT_LIMIT:
                break

        cleaned_paths = [str(path) for path in paths]
        changed = (
            not isinstance(raw_value, list)
            or cleaned_paths != [str(path) for path in raw_paths]
        )
        config["recent_projects"] = cleaned_paths
        if self.safe_project_path(config.get("last_project")) is None:
            if config.get("last_project"):
                changed = True
            config["last_project"] = ""
        return RecentProjects(tuple(paths), changed)
