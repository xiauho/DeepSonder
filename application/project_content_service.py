"""Read-only project navigation and recoverable local-data operations."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.project import NovelProject
from core.project_data import ProjectDataStore, normalize_canon_entry_kind


@dataclass(frozen=True)
class ProjectDocumentItem:
    item_id: str
    kind: str
    category: str
    title: str
    path: str
    relative_path: str
    protected: bool = False
    importance: str | None = None


@dataclass(frozen=True)
class ProjectContentSnapshot:
    chapters: tuple[ProjectDocumentItem, ...]
    outlines: tuple[ProjectDocumentItem, ...]
    characters: tuple[ProjectDocumentItem, ...]
    world: tuple[ProjectDocumentItem, ...]
    power: tuple[ProjectDocumentItem, ...]
    timeline: tuple[ProjectDocumentItem, ...]
    next_chapter_id: str

    @property
    def item_count(self) -> int:
        return sum(
            len(group)
            for group in (
                self.chapters,
                self.outlines,
                self.characters,
                self.world,
                self.power,
                self.timeline,
            )
        )


@dataclass(frozen=True)
class TrashItem:
    trash_id: str
    kind: str
    title: str
    original_path: str
    deleted_at: str
    can_rename: bool


@dataclass(frozen=True)
class TrashSnapshot:
    items: tuple[TrashItem, ...]


class ProjectContentService:
    """Build navigation DTOs and coordinate the existing recycle-bin stores."""

    def snapshot(
        self,
        project: NovelProject,
        store: ProjectDataStore,
    ) -> ProjectContentSnapshot:
        outlines = (
            self._item(project, store, project.outline_dir / "main_arc.md", "outline", "大纲"),
            self._item(project, store, project.outline_dir / "future_plan.md", "plan", "规划"),
            self._item(project, store, project.style_guide_path, "style", "写作设置"),
        )
        timeline_path = project.canon_dir / "timeline.md"
        timeline = (
            (self._item(project, store, timeline_path, "timeline", "时间线"),)
            if timeline_path.is_file()
            else ()
        )
        return ProjectContentSnapshot(
            chapters=tuple(
                self._item(project, store, path, "chapter", "章节")
                for path in store.list_chapters()
            ),
            outlines=outlines,
            characters=tuple(
                self._item(project, store, path, "character", "角色")
                for path in store.list_characters()
            ),
            world=tuple(
                self._item(project, store, path, "world", "世界观")
                for path in store.list_world()
            ),
            power=tuple(
                self._item(
                    project,
                    store,
                    path,
                    "power",
                    "体系",
                    protected=store.is_core_power_path(path),
                    importance=(
                        "core"
                        if store.is_core_power_path(path)
                        else store.system_metadata(path).get("importance", "non_core")
                    ),
                )
                for path in store.list_power()
            ),
            timeline=timeline,
            next_chapter_id=self._next_chapter_id(project),
        )

    def trash_snapshot(self, store: ProjectDataStore) -> TrashSnapshot:
        items: list[TrashItem] = []
        items.extend(
            TrashItem(
                item.trash_id,
                "chapter",
                item.title,
                item.original_path,
                item.deleted_at,
                True,
            )
            for item in store.list_trash()
        )
        items.extend(
            TrashItem(
                item.trash_id,
                "character",
                item.title,
                item.original_path,
                item.deleted_at,
                True,
            )
            for item in store.list_character_trash()
        )
        items.extend(
            TrashItem(
                item.trash_id,
                item.kind,
                item.title,
                item.original_path,
                item.deleted_at,
                item.kind != "timeline",
            )
            for item in store.list_canon_trash()
        )
        items.sort(key=lambda item: item.deleted_at, reverse=True)
        return TrashSnapshot(tuple(items))

    def restore_trash_item(
        self,
        project: NovelProject,
        store: ProjectDataStore,
        kind: str,
        trash_id: str,
        *,
        conflict_policy: str = "error",
    ) -> Path:
        kind = self._trash_kind(kind)
        policy = str(conflict_policy or "error").strip().casefold()
        if policy not in {"error", "rename"}:
            raise ValueError("回收站恢复策略无效。")
        if kind == "chapter":
            return store.restore_trash_item(trash_id, conflict_policy=policy)
        if kind == "character":
            return store.restore_character_trash_item(
                trash_id, conflict_policy=policy
            )
        if kind == "world":
            return store.restore_world_trash_item(
                trash_id, conflict_policy=policy
            )
        if kind == "power":
            return store.restore_power_trash_item(
                trash_id, conflict_policy=policy
            )
        return store.restore_timeline_trash_item(
            trash_id, conflict_policy=policy
        )

    def delete_trash_item_forever(
        self,
        store: ProjectDataStore,
        kind: str,
        trash_id: str,
    ) -> None:
        kind = self._trash_kind(kind)
        if kind == "chapter":
            store.delete_trash_item(trash_id)
        elif kind == "character":
            store.delete_character_trash_item(trash_id)
        elif kind == "world":
            store.delete_world_trash_item(trash_id)
        elif kind == "power":
            store.delete_power_trash_item(trash_id)
        else:
            store.delete_timeline_trash_item(trash_id)

    def set_system_importance(
        self,
        store: ProjectDataStore,
        path: Path | str,
        importance: str,
    ) -> Path:
        target = Path(path)
        if not target.is_absolute():
            target = store.root / target
        store.set_system_importance(target, importance)
        return target.resolve()

    @staticmethod
    def _next_chapter_id(project: NovelProject) -> str:
        from core.project_data import next_available_chapter_id

        return next_available_chapter_id(project)

    @staticmethod
    def _trash_kind(kind: str) -> str:
        normalized = str(kind or "").strip().casefold()
        if normalized in {"chapter", "character"}:
            return normalized
        normalized = normalize_canon_entry_kind(normalized)
        if normalized not in {"world", "power", "timeline"}:
            raise ValueError("不支持的回收站条目类型。")
        return normalized

    @staticmethod
    def _item(
        project: NovelProject,
        store: ProjectDataStore,
        path: Path,
        kind: str,
        category: str,
        *,
        protected: bool = False,
        importance: str | None = None,
    ) -> ProjectDocumentItem:
        resolved = path.resolve()
        return ProjectDocumentItem(
            item_id=resolved.stem,
            kind=kind,
            category=category,
            title=store.chapter_display_name(resolved),
            path=str(resolved),
            relative_path=resolved.relative_to(project.root).as_posix(),
            protected=protected,
            importance=importance,
        )
