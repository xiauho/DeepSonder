"""Document lifecycle and project-file operations for the desktop UI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from application.document_service import (
    DocumentRevisionConflict,
    DocumentService,
    DocumentServiceError,
)
from core.project_data import (
    normalize_canon_entry_kind,
    sanitize_filename,
)
from ui.editor import Editor
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
        document_service: DocumentService | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.editor = editor
        self.project_session = project_session
        self.document_service = document_service or DocumentService()
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
        if current and Path(current).resolve() == path.resolve():
            return True
        if not self.save_if_dirty():
            return False
        try:
            snapshot = self.document_service.open_document(
                self._require_project(), category, path
            )
        except DocumentServiceError as exc:
            self.editor.clear_document(str(exc))
            return False
        return bool(self.editor.load_document(snapshot))

    @property
    def save_conflict_path(self) -> Path | None:
        return self._save_conflict_path

    def save(self, *, force: bool = False) -> bool:
        path = self.editor.current_path()
        self._save_conflict_path = None
        if not path:
            return False
        revision = self.editor.loaded_revision()
        expected_revision = revision if isinstance(revision, str) else None
        try:
            saved = self.document_service.save_document(
                self._require_project(),
                self.editor.current_category(),
                path,
                self.editor.document_text(),
                expected_revision=expected_revision,
                force=force,
            )
        except DocumentRevisionConflict as exc:
            self._save_conflict_path = exc.path
            self.save_conflict_detected.emit(str(exc.path))
            return False
        except (DocumentServiceError, OSError) as exc:
            show_error = getattr(self.editor, "show_save_error", None)
            if callable(show_error):
                show_error(str(exc))
            return False
        self.editor.mark_saved(saved.revision)
        self.project_session.notify_data_changed([path], kind="file")
        return True

    def reload_current_file(self) -> bool:
        path = self.editor.current_path()
        if not path:
            return False
        try:
            snapshot = self.document_service.open_document(
                self._require_project(), self.editor.current_category(), path
            )
        except DocumentServiceError:
            return False
        self.editor.load_document(snapshot)
        self._save_conflict_path = None
        return True

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
        return self.document_service.next_chapter_id(self._require_project())

    def create_chapter(self, title: str, chapter_id: str) -> Path:
        project = self._require_project()
        mutation = self.document_service.create_chapter(
            project,
            self.project_session.require_data_store(),
            title,
            chapter_id,
        )
        self._notify_mutation(mutation)
        return Path(mutation.result_path)

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

        mutation = self.document_service.delete_chapter(
            project, self.project_session.require_data_store(), chapter_id
        )
        if is_current:
            self.editor.clear_document("章节已删除")
        self._notify_mutation(mutation)
        return Path(mutation.result_path)

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

        mutation = self.document_service.delete_character(
            project, self.project_session.require_data_store(), raw_id
        )
        if is_current:
            self.editor.clear_document("角色卡已删除")
        self._notify_mutation(mutation)
        return Path(mutation.result_path)

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

        mutation = self.document_service.delete_canon_entry(
            project,
            self.project_session.require_data_store(),
            entry_kind,
            target,
        )
        if is_current:
            self.editor.clear_document("故事资料已删除")
        self._notify_mutation(mutation)
        return Path(mutation.result_path)

    def create_character(self, name: str) -> Path:
        return self.create_canon_entry("character", name)

    def create_canon_entry(self, kind: str, title: str) -> Path:
        """Create a templated story-data entry and publish the change."""
        project = self._require_project()
        entry_kind = normalize_canon_entry_kind(kind)
        mutation = self.document_service.create_canon_entry(
            project,
            self.project_session.require_data_store(),
            entry_kind,
            title,
        )
        self._notify_mutation(mutation)
        return Path(mutation.result_path)

    def create_world_entry(self, title: str) -> Path:
        return self.create_canon_entry("world", title)

    def create_power_entry(self, title: str) -> Path:
        return self.create_canon_entry("power", title)

    def create_timeline(self) -> Path:
        """Create the singleton timeline document when it is missing."""
        project = self._require_project()
        mutation = self.document_service.create_timeline(
            project, self.project_session.require_data_store()
        )
        self._notify_mutation(mutation)
        return Path(mutation.result_path)

    def import_markdown(self, sources: list[Path | str]) -> list[Path]:
        project = self._require_project()
        mutations = self.document_service.import_markdown(
            project, self.project_session.require_data_store(), sources
        )
        imported = [Path(item.result_path) for item in mutations]
        if mutations:
            self.project_session.notify_data_changed(imported, kind="chapter")
        return imported

    def _notify_mutation(self, mutation) -> None:
        self.project_session.notify_data_changed(
            mutation.changed_paths,
            kind=mutation.kind,
        )

    def _require_project(self):
        project = self.project
        if project is None:
            raise RuntimeError("当前没有打开的项目。")
        return project

    @staticmethod
    def safe_name(value: str) -> str:
        """Compatibility wrapper for callers using the old controller API."""
        return sanitize_filename(value)
