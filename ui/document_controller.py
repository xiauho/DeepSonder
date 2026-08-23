"""Document lifecycle and project-file operations for the desktop UI."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from core.project_data import ProjectDataStore
from ui.editor import Editor
from ui.project_session import ProjectSession


class DocumentController(QObject):
    """Coordinate editor files without owning dialogs or page navigation."""

    document_saved = Signal(str)
    auto_saved = Signal(str)

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

    def save(self) -> bool:
        return bool(self.editor.save())

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
        project = self._require_project()
        store = ProjectDataStore(project)
        return f"chapter_{len(store.list_chapters()) + 1:02d}"

    def create_chapter(self, title: str, chapter_id: str) -> Path:
        project = self._require_project()
        title = str(title).strip()
        raw_chapter_id = str(chapter_id).strip()
        if not title or not raw_chapter_id:
            raise ValueError("章节标题和文件标识不能为空。")
        chapter_id = self.safe_name(raw_chapter_id)
        path = project.chapters_dir / f"{chapter_id}.md"
        ProjectDataStore(project).write_new_file(
            path,
            f"# {title}\n\n## 大纲\n- 本章目标：\n- 核心冲突：\n- 章节钩子：\n\n## 剧情简写\n\n\n## 正文\n\n",
        )
        self.project_session.notify_data_changed()
        return path

    def create_character(self, name: str) -> Path:
        project = self._require_project()
        name = str(name).strip()
        if not name:
            raise ValueError("角色姓名不能为空。")
        path = project.canon_dir / "characters" / f"{self.safe_name(name)}.md"
        ProjectDataStore(project).write_new_file(
            path,
            f"# {name}\n\n- 身份：\n- 外貌特征：\n- 性格：\n- 核心欲望：\n- 当前目标：\n- 战力/能力：\n- 关键关系：\n- 秘密：\n",
        )
        self.project_session.notify_data_changed()
        return path

    def create_world_entry(self, title: str) -> Path:
        project = self._require_project()
        title = str(title).strip()
        if not title:
            raise ValueError("世界观条目名称不能为空。")
        path = project.canon_dir / "world" / f"{self.safe_name(title)}.md"
        ProjectDataStore(project).write_new_file(
            path,
            f"# {title}\n\n## 核心规则\n\n## 历史与现状\n\n## 对剧情的约束\n",
        )
        self.project_session.notify_data_changed()
        return path

    def import_markdown(self, sources: list[Path | str]) -> list[Path]:
        project = self._require_project()
        store = ProjectDataStore(project)
        imported: list[Path] = []
        for source_value in sources:
            source = Path(source_value)
            stem = self.safe_name(source.stem) or "imported_chapter"
            destination = project.chapters_dir / f"{stem}.md"
            suffix = 2
            while destination.exists():
                destination = project.chapters_dir / f"{stem}_{suffix}.md"
                suffix += 1
            text = self._read_import_text(source)
            if not text.lstrip().startswith("# "):
                text = f"# {source.stem}\n\n## 正文\n\n{text.strip()}\n"
            # The facade keeps the write inside the active project and avoids
            # overwriting an existing destination.
            store.write_new_file(destination, text)
            imported.append(destination)
        if imported:
            self.project_session.notify_data_changed()
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
        forbidden = '<>:"/\\|?*'
        cleaned = "".join("_" if char in forbidden else char for char in value).strip(" .")
        return cleaned or "untitled"
