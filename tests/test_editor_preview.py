import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import SkipTest, TestCase

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QUrl  # noqa: E402
from PySide6.QtGui import QTextCursor, QTextDocument  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.editor import Editor, markdown_preview_source  # noqa: E402


class EditorPreviewTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        instance = QCoreApplication.instance()
        if instance is not None and not isinstance(instance, QApplication):
            raise SkipTest("已有 QCoreApplication，无法在同一进程升级为 QApplication")
        cls.app = instance or QApplication([])

    def test_timeline_notes_title_and_empty_editor_survive_document_switches(self) -> None:
        from application.document_service import DocumentService
        from core.project import NovelProject

        with TemporaryDirectory() as tmp:
            project = NovelProject.create(Path(tmp) / "proj", "测试")
            service = DocumentService()
            path = project.canon_dir / "timeline.md"
            chapter = project.chapters_dir / "chapter_01.md"
            editor = Editor()
            self.addCleanup(editor.close)
            for _ in range(2):
                editor.load_document(service.open_document(project, "章节", chapter))
                self.assertIn("章节建议保留", editor.text_edit.placeholderText())
                editor.load_document(service.open_document(project, "时间线", path))
                self.assertEqual(editor.title_label.text(), "时间线笔记")
                self.assertEqual(editor.text_edit.placeholderText(), "")
                self.assertEqual(editor.text_edit.toPlainText(), "")
                self.assertFalse(editor.is_dirty())
                editor.set_view_mode("preview")
                self.assertEqual(editor.preview_browser.toPlainText(), "")
                editor.set_view_mode("source")
            self.assertEqual(path.read_bytes(), b"")
            editor.open_file("时间线", str(path))
            self.assertEqual(editor.title_label.text(), "时间线笔记")
            editor.load_document(service.open_document(project, "章节", chapter))
            editor.clear_document()
            self.assertEqual(editor.text_edit.placeholderText(), "")

    def test_preview_filter_only_removes_novalist_comments(self) -> None:
        source = (
            "<!-- novalist:character-card:v2 -->\n"
            "# 林夜\n"
            "<!-- 作者备注 -->\n"
            "<!-- novalist:auto-state:v1:start -->\n"
            "- 当前状态：警戒\n"
            "<!-- novalist:auto-state:v1:end -->\n"
        )

        filtered = markdown_preview_source(source)

        self.assertNotIn("novalist:", filtered)
        self.assertIn("<!-- 作者备注 -->", filtered)
        self.assertIn("- 当前状态：警戒", filtered)

    def test_preview_renders_unsaved_source_without_mutating_it(self) -> None:
        editor = Editor()
        source = (
            "# 林夜\n\n"
            "## 当前剧情状态（DeepSonder 同步）\n\n"
            "<!-- novalist:auto-state:v1:start -->\n"
            "- 当前状态：警戒\n"
            "<!-- novalist:auto-state:v1:end -->\n"
        )
        editor.text_edit.setPlainText(source)
        dirty_before = editor.is_dirty()

        editor.set_view_mode("preview")

        preview = editor.preview_browser.toPlainText()
        self.assertEqual(editor.view_mode(), "preview")
        self.assertIs(editor.editor_stack.currentWidget(), editor.preview_browser)
        self.assertIn("当前剧情状态", preview)
        self.assertIn("当前状态：警戒", preview)
        self.assertNotIn("##", preview)
        self.assertNotIn("novalist:auto-state", preview)
        self.assertEqual(editor.text_edit.toPlainText(), source)
        self.assertEqual(editor.is_dirty(), dirty_before)

    def test_switching_views_preserves_cursor_and_undo_history(self) -> None:
        editor = Editor()
        editor.text_edit.setPlainText("# 标题\n\n正文")
        cursor = editor.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("新增")
        position = cursor.position()
        editor.text_edit.setTextCursor(cursor)
        self.assertTrue(editor.text_edit.document().isUndoAvailable())

        editor.set_view_mode("preview")
        editor.set_view_mode("source")

        self.assertEqual(editor.text_edit.textCursor().position(), position)
        self.assertTrue(editor.text_edit.document().isUndoAvailable())
        editor.undo()
        self.assertNotIn("新增", editor.text_edit.toPlainText())

    def test_find_switches_preview_back_to_source(self) -> None:
        editor = Editor()
        editor.text_edit.setPlainText("# 标题\n\n正文")
        editor.set_view_mode("preview")

        editor.show_find()

        self.assertEqual(editor.view_mode(), "source")
        self.assertIs(editor.editor_stack.currentWidget(), editor.text_edit)
        self.assertFalse(editor.find_bar.isHidden())

    def test_open_and_save_while_preview_is_active_use_source_text(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "character.md"
            path.write_text("# 初始角色\n", encoding="utf-8")
            editor = Editor()
            editor.set_view_mode("preview")

            self.assertTrue(editor.open_file("角色", str(path)))
            self.assertIn("初始角色", editor.preview_browser.toPlainText())
            editor.text_edit.setPlainText("# 修改角色\n")
            self.assertTrue(editor.save())

            self.assertEqual(path.read_text(encoding="utf-8"), "# 修改角色\n")
            self.assertEqual(editor.view_mode(), "preview")

    def test_preview_blocks_remote_resources_and_refreshes_theme(self) -> None:
        editor = Editor()
        light_css = editor.preview_browser.document().defaultStyleSheet()

        editor.set_theme({"theme": "dark"})

        dark_css = editor.preview_browser.document().defaultStyleSheet()
        self.assertNotEqual(light_css, dark_css)
        self.assertIsNone(
            editor.preview_browser.loadResource(
                QTextDocument.ResourceType.ImageResource,
                QUrl("https://example.com/image.png"),
            )
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
