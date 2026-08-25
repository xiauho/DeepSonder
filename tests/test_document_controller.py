import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, Signal

from core.project import NovelProject
from core.project_data import ChapterIdConflictError
from ui.document_controller import DocumentController
from ui.project_session import ProjectSession


class FakeEditor(QObject):
    file_saved = Signal(str)

    def __init__(self):
        super().__init__()
        self.path: str | None = None
        self.category = ""
        self.dirty = False
        self.content = ""
        self.save_calls = 0
        self.cleared = []
        self.external_change = False
        self.reload_calls = 0

    def current_path(self):
        return self.path

    def is_dirty(self):
        return self.dirty

    def open_file(self, category: str, path: str) -> bool:
        self.category = category
        self.path = path
        self.content = Path(path).read_text(encoding="utf-8")
        self.dirty = False
        return True

    def save(self, *, force: bool = False) -> bool:
        self.save_calls += 1
        if self.path is None:
            return False
        Path(self.path).write_text(self.content, encoding="utf-8")
        self.dirty = False
        self.file_saved.emit(self.path)
        return True

    def has_external_change(self):
        return self.external_change

    def reload_current_file(self) -> bool:
        self.reload_calls += 1
        self.content = Path(self.path).read_text(encoding="utf-8")
        self.dirty = False
        return True

    def clear_document(self, message: str = "未打开文件") -> None:
        self.cleared.append(message)
        self.path = None
        self.category = ""
        self.content = ""
        self.dirty = False


class DocumentControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_open_saves_dirty_document_before_switching(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            first = project.chapters_dir / "chapter_01.md"
            second = project.chapters_dir / "chapter_02.md"
            second.write_text("# 第二章\n\n## 正文\n新内容\n", encoding="utf-8")
            editor = FakeEditor()
            editor.path = str(first)
            editor.content = "已修改"
            editor.dirty = True
            controller = DocumentController(editor, ProjectSession())

            self.assertTrue(controller.open_file("章节", second))
            self.assertEqual(editor.save_calls, 1)
            self.assertEqual(editor.path, str(second))
            self.assertEqual(first.read_text(encoding="utf-8"), "已修改")

    def test_create_and_import_documents_notify_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            changes = []
            session.data_changed.connect(lambda value: changes.append(value))
            controller = DocumentController(FakeEditor(), session)
            session.set_project(project)

            chapter = controller.create_chapter("新章节", "chapter_02")
            character = controller.create_character("新角色")
            world = controller.create_world_entry("新设定")
            source = Path(tmp) / "import.md"
            source.write_text("# 外部章节\n\n正文\n", encoding="utf-8")
            imported = controller.import_markdown([source])

            self.assertTrue(chapter.exists())
            self.assertTrue(character.exists())
            self.assertTrue(world.exists())
            self.assertEqual(len(imported), 1)
            self.assertEqual(len(changes), 4)

    def test_next_chapter_id_does_not_reuse_existing_number(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            (project.chapters_dir / "chapter_03.md").write_text(
                "# 第三章\n", encoding="utf-8"
            )
            session = ProjectSession()
            session.set_project(project)
            controller = DocumentController(FakeEditor(), session)

            self.assertEqual(controller.next_chapter_id(), "chapter_04")

    def test_create_chapter_reports_suggestion_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            existing = project.chapters_dir / "chapter_02.md"
            existing.write_text("# 原章节\n", encoding="utf-8")
            session = ProjectSession()
            session.set_project(project)
            controller = DocumentController(FakeEditor(), session)

            with self.assertRaises(ChapterIdConflictError) as raised:
                controller.create_chapter("新章节", "Chapter_02")

            self.assertEqual(raised.exception.suggested_id, "Chapter_02_2")
            self.assertEqual(existing.read_text(encoding="utf-8"), "# 原章节\n")

    def test_auto_save_reports_success_only_after_save(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            editor = FakeEditor()
            editor.path = str(project.chapters_dir / "chapter_01.md")
            editor.content = "自动保存内容"
            editor.dirty = True
            controller = DocumentController(editor, ProjectSession())
            saved = []
            controller.auto_saved.connect(saved.append)

            self.assertTrue(controller.auto_save())
            self.assertEqual(saved, [editor.path])
            self.assertFalse(editor.dirty)

    def test_save_blocks_external_overwrite_until_forced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            editor = FakeEditor()
            editor.path = str(project.chapters_dir / "chapter_01.md")
            editor.content = "本地修改"
            editor.dirty = True
            editor.external_change = True
            controller = DocumentController(editor, ProjectSession())

            self.assertFalse(controller.save())
            self.assertEqual(editor.save_calls, 0)
            self.assertEqual(controller.save_conflict_path, Path(editor.path))

            editor.external_change = False
            self.assertTrue(controller.save(force=True))
            self.assertIsNone(controller.save_conflict_path)
            self.assertEqual(editor.save_calls, 1)

    def test_reload_current_file_clears_save_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            editor = FakeEditor()
            editor.path = str(project.chapters_dir / "chapter_01.md")
            editor.external_change = True
            controller = DocumentController(editor, ProjectSession())

            self.assertFalse(controller.save())
            self.assertTrue(controller.reload_current_file())
            self.assertEqual(editor.reload_calls, 1)
            self.assertIsNone(controller.save_conflict_path)

    def test_delete_current_chapter_clears_editor_and_notifies_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            session.set_project(project)
            editor = FakeEditor()
            editor.path = str(project.chapters_dir / "chapter_01.md")
            editor.category = "章节"
            controller = DocumentController(editor, session)
            changes = []
            session.data_changed.connect(changes.append)

            controller.delete_chapter("chapter_01")

            self.assertIsNone(editor.path)
            self.assertFalse((project.chapters_dir / "chapter_01.md").exists())
            self.assertEqual(editor.cleared, ["章节已删除"])
            self.assertEqual(changes, [project])

    def test_delete_current_dirty_chapter_requires_explicit_discard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            session = ProjectSession()
            session.set_project(project)
            editor = FakeEditor()
            editor.path = str(project.chapters_dir / "chapter_01.md")
            editor.category = "章节"
            editor.dirty = True
            controller = DocumentController(editor, session)

            with self.assertRaisesRegex(RuntimeError, "未保存修改"):
                controller.delete_chapter("chapter_01")
            self.assertTrue(Path(editor.path).exists())
