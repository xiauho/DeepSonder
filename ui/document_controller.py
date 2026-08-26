"""Document lifecycle and project-file operations for the desktop UI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from core.project_data import (
    ChapterIdConflictError,
    chapter_id_exists,
    normalize_canon_entry_kind,
    next_available_chapter_id,
    sanitize_filename,
)
from ui.editor import Editor, ExternalFileChangedError
from ui.project_session import ProjectSession


class DocumentController(QObject):
    """Coordinate editor files without owning dialogs or page navigation."""

    document_saved = Signal(str)
    auto_saved = Signal(str)
    save_conflict_detected = Signal(str)

    def __init__(
        self,
        editor: Editor,
        project_session: ProjectSession,
        parent=None,
    ):
        super().__init__(parent)
        self.editor = editor
        self.project_session = project_session
        self.editor.file_saved.connect(self.document_saved)

        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.timeout.connect(self.auto_save)
        self._save_conflict_path: Path | None = None

    @property
    def project(self):
        return self.project_session.project

    def configure_auto_save(self, enabled: bool, interval_seconds: int) -> None:
        try:
            seconds = max(5, min(600, int(interval_seconds)))
        except (TypeError, ValueError):
            seconds = 30
        if enabled:
            self._auto_save_timer.start(seconds * 1000)
        else:
            self._auto_save_timer.stop()

    def open_file(self, category: str, path: Path | str) -> bool:
        path = Path(path)
        current = self.editor.current_path()
        if current and Path(current) == path:
            return True
        if not self.save_if_dirty():
            return False
        return self.editor.open_file(category, str(path))

    @property
    def save_conflict_path(self) -> Path | None:
        return self._save_conflict_path

    def save(self, *, force: bool = False) -> bool:
        path = self.editor.current_path()
        self._save_conflict_path = None
        if not force and path:
            has_external_change = getattr(self.editor, "has_external_change", None)
            if callable(has_external_change) and has_external_change():
                self._save_conflict_path = Path(path)
                self.save_conflict_detected.emit(str(path))
                return False
        try:
            saved = bool(self.editor.save(force=force) if force else self.editor.save())
        except ExternalFileChangedError as exc:
            self._save_conflict_path = exc.path
            self.save_conflict_detected.emit(str(exc.path))
            return False
        if saved and path and self.project is not None:
            self.project_session.notify_data_changed([path], kind="file")
        return saved

    def reload_current_file(self) -> bool:
        reload_file = getattr(self.editor, "reload_current_file", None)
        if not callable(reload_file):
            return False
        reloaded = bool(reload_file())
        if reloaded:
            self._save_conflict_path = None
        return reloaded

    def save_if_dirty(self) -> bool:
        return not self.editor.is_dirty() or self.save()

    def auto_save(self) -> bool:
        if not self.editor.is_dirty() or not self.editor.current_path():
            return False
        path = self.editor.current_path()
        if not self.save():
            return False
        if path:
            self.auto_saved.emit(str(path))
        return True

    def next_chapter_id(self) -> str:
        store = self.project_session.require_data_store()
        return next_available_chapter_id(store.project)

    def create_chapter(self, title: str, chapter_id: str) -> Path:
        project = self._require_project()
        title = str(title).strip()
        raw_chapter_id = str(chapter_id).strip()
        if not title or not raw_chapter_id:
            raise ValueError("章节标题和文件标识不能为空。")
        chapter_id = sanitize_filename(raw_chapter_id)
        path = project.chapters_dir / f"{chapter_id}.md"
        if chapter_id_exists(project, chapter_id):
            raise ChapterIdConflictError(
                chapter_id,
                next_available_chapter_id(project, chapter_id),
                path,
            )
        self.project_session.require_data_store().write_new_file(
            path,
            f"# {title}\n\n## 大纲\n- 本章目标：\n- 核心冲突：\n- 章节钩子：\n\n## 剧情简写\n\n\n## 正文\n\n",
        )
        self.project_session.notify_data_changed([path], kind="chapter")
        return path

    def delete_chapter(
        self,
        chapter_id: str,
        *,
        discard_current_changes: bool = False,
    ) -> Path:
        """Delete a chapter after the UI has handled its confirmation."""
        project = self._require_project()
        target = project.chapters_dir / f"{str(chapter_id).strip()}.md"
        current = self.editor.current_path()
        is_current = bool(current and Path(current).resolve() == target.resolve())
        if is_current and self.editor.is_dirty() and not discard_current_changes:
            raise RuntimeError("当前章节存在未保存修改，请先保存或放弃修改。")

        deleted = self.project_session.require_data_store().delete_chapter(chapter_id)
        if is_current:
            self.editor.clear_document("章节已删除")
        self.project_session.notify_data_changed(
            [target, project.memory_dir / "chapter_summaries.json"],
            kind="chapter",
        )
        return deleted

    def delete_character(
        self,
        character_id: str,
        *,
        discard_current_changes: bool = False,
    ) -> Path:
        """Move a character card into the recycle bin after editor checks."""
        project = self._require_project()
        raw_id = str(character_id or "").strip()
        target = project.canon_dir / "characters" / f"{raw_id}.md"
        current = self.editor.current_path()
        is_current = bool(current and Path(current).resolve() == target.resolve())
        if is_current and self.editor.is_dirty() and not discard_current_changes:
            raise RuntimeError("当前角色卡存在未保存修改，请先保存或放弃修改。")

        deleted = self.project_session.require_data_store().delete_character(raw_id)
        if is_current:
            self.editor.clear_document("角色卡已删除")
        self.project_session.notify_data_changed([target], kind="canon")
        return deleted

    def delete_canon_entry(
        self,
        kind: str,
        path: Path | str,
        *,
        discard_current_changes: bool = False,
    ) -> Path:
        """Move a world, ordinary power, or timeline document to trash."""
        project = self._require_project()
        entry_kind = normalize_canon_entry_kind(kind)
        if entry_kind == "character":
            raise ValueError("角色卡请使用角色卡专用删除接口。")
        target = Path(path)
        if not target.is_absolute():
            target = project.root / target
        current = self.editor.current_path()
        is_current = bool(current and Path(current).resolve() == target.resolve())
        if is_current and self.editor.is_dirty() and not discard_current_changes:
            raise RuntimeError("当前故事资料存在未保存修改，请先保存或放弃修改。")

        deleted = self.project_session.require_data_store().delete_canon_entry(
            entry_kind, target
        )
        if is_current:
            self.editor.clear_document("故事资料已删除")
        changed_paths = [target]
        if entry_kind == "power":
            changed_paths.append(
                self.project_session.require_data_store().system_registry_path
            )
        self.project_session.notify_data_changed(changed_paths, kind="canon")
        return deleted

    def create_character(self, name: str) -> Path:
        return self.create_canon_entry("character", name)

    def create_canon_entry(self, kind: str, title: str) -> Path:
        """Create a templated story-data entry and publish the change."""
        self._require_project()
        entry_kind = normalize_canon_entry_kind(kind)
        path = self.project_session.require_data_store().create_canon_entry(
            entry_kind, title
        )
        self.project_session.notify_data_changed([path], kind="canon")
        return path

    def create_world_entry(self, title: str) -> Path:
        return self.create_canon_entry("world", title)

    def create_power_entry(self, title: str) -> Path:
        return self.create_canon_entry("power", title)

    def create_timeline(self) -> Path:
        """Create the singleton timeline document when it is missing."""
        self._require_project()
        path = self.project_session.require_data_store().create_timeline()
        self.project_session.notify_data_changed([path], kind="canon")
        return path

    def import_markdown(self, sources: list[Path | str]) -> list[Path]:
        project = self._require_project()
        store = self.project_session.require_data_store()
        imported: list[Path] = []
        for source_value in sources:
            source = Path(source_value)
            stem = sanitize_filename(source.stem) or "imported_chapter"
            chapter_id = next_available_chapter_id(project, stem)
            destination = project.chapters_dir / f"{chapter_id}.md"
            text = self._read_import_text(source)
            if not text.lstrip().startswith("# "):
                text = f"# {source.stem}\n\n## 正文\n\n{text.strip()}\n"
            # The facade keeps the write inside the active project and avoids
            # overwriting an existing destination.
            store.write_new_file(destination, text)
            imported.append(destination)
        if imported:
            self.project_session.notify_data_changed(imported, kind="chapter")
        return imported

    @staticmethod
    def _read_import_text(source: Path) -> str:
        try:
            return source.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return source.read_text(encoding="gb18030")

    def _require_project(self):
        project = self.project
        if project is None:
            raise RuntimeError("当前没有打开的项目。")
        return project

    @staticmethod
    def safe_name(value: str) -> str:
        """Compatibility wrapper for callers using the old controller API."""
        return sanitize_filename(value)
