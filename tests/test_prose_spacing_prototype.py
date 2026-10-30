"""Safety gates carried forward from the isolated line-spacing experiment."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFont, QFontDatabase, QInputMethodEvent, QPalette, QTextBlockFormat, QTextCursor
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QTextEdit

from application.document_service import DocumentService
from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from core.task_context import AIContextSnapshot
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from ui.prose_edit import ProseTextEdit as SpacedProseEdit
from ui.editor import Editor
from ui.theme import apply_theme


class ProseSpacingPrototypeTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        path = Path("C:/Windows/Fonts/msyh.ttc")
        cls.font_id = QFontDatabase.addApplicationFont(str(path)) if path.exists() else -1

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        self.edit = SpacedProseEdit()
        self.addCleanup(self.edit.deleteLater)
        self.addCleanup(self.edit.close)
        font = QFont("Microsoft YaHei")
        font.setPixelSize(18)
        self.edit.setFont(font)
        self.edit.resize(640, 420)
        self.source = ("旧站的灯还亮着，山后有人在等他。 Mixed text 123。" * 16 + "\n\n") * 60
        self.edit.setPlainText(self.source)
        self.edit.show()
        self.settle()

    def settle(self):
        self.app.processEvents()

    def state(self):
        doc = self.edit.document()
        cursor = self.edit.textCursor()
        return (self.edit.toPlainText(), doc.revision(), doc.isModified(),
                doc.availableUndoSteps(), doc.availableRedoSteps(), cursor.position(), cursor.anchor())

    def test_negative_control_paragraph_format_changes_history_without_spacing(self):
        edit = QPlainTextEdit("第一段\n第二段")
        self.addCleanup(edit.deleteLater)
        edit.resize(500, 300)
        edit.show()
        self.addCleanup(edit.close)
        self.settle()
        before = edit.blockBoundingRect(edit.document().firstBlock()).height()
        cursor = edit.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        fmt = QTextBlockFormat()
        fmt.setLineHeight(180, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(fmt)
        self.settle()
        self.assertEqual(edit.blockBoundingRect(edit.document().firstBlock()).height(), before)
        self.assertTrue(edit.document().isModified())
        self.assertGreater(edit.document().availableUndoSteps(), 0)

    def test_clean_document_spacing_emits_no_content_or_modified_signal(self):
        before = self.state()
        text = QSignalSpy(self.edit.textChanged)
        changed = QSignalSpy(self.edit.document().contentsChanged)
        modified = QSignalSpy(self.edit.document().modificationChanged)
        for factor in (1.8, 2.4, 1.2, 1.0):
            self.edit.set_line_spacing(factor)
            self.settle()
            self.assertEqual(self.state(), before)
        self.assertEqual((text.count(), changed.count(), modified.count()), (0, 0, 0))

    def test_dirty_document_preserves_selection_undo_and_redo(self):
        cursor = self.edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("第一笔。")
        cursor.beginEditBlock()
        cursor.insertText("第二笔。")
        cursor.endEditBlock()
        self.edit.undo()
        self.assertTrue(self.edit.document().isRedoAvailable())
        cursor.setPosition(9)
        cursor.setPosition(25, QTextCursor.MoveMode.KeepAnchor)
        self.edit.setTextCursor(cursor)
        before = self.state()
        for factor in (1.8, 2.4, 1):
            self.edit.set_line_spacing(factor)
            self.settle()
            self.assertEqual(self.state(), before)
        self.edit.redo()
        self.assertTrue(self.edit.toPlainText().endswith("第一笔。第二笔。"))
        self.edit.undo()
        self.edit.undo()
        self.assertEqual(self.edit.toPlainText(), self.source)

    def test_wrapped_lines_pitch_and_mouse_hits_follow_spacing(self):
        block = self.edit.document().firstBlock()
        self.edit.blockBoundingRect(block)
        natural_pitch = block.layout().lineAt(1).y()
        for factor in (1.8, 2.4, 1.0):
            self.edit.set_line_spacing(factor)
            rect = self.edit.blockBoundingRect(block)
            layout = block.layout()
            self.assertAlmostEqual(layout.lineAt(1).y(), natural_pitch * factor, delta=0.1)
            self.assertGreaterEqual(rect.height(), layout.boundingRect().height())
            for index in range(min(3, layout.lineCount())):
                cursor = QTextCursor(block)
                cursor.setPosition(block.position() + layout.lineAt(index).textStart() + 3)
                pixel = self.edit.cursorRect(cursor).center()
                self.assertEqual(self.edit.cursorForPosition(pixel).position(), cursor.position())
                QTest.mouseClick(self.edit.viewport(), Qt.MouseButton.LeftButton, pos=pixel)
                self.assertEqual(self.edit.textCursor().position(), cursor.position())

    def test_scroll_anchor_and_last_line_remain_reachable(self):
        bar = self.edit.verticalScrollBar()
        bar.setValue(70)
        self.settle()
        anchor = self.edit.cursorForPosition(QPoint(5, 5)).position()
        block = self.edit.firstVisibleBlock().blockNumber()
        value = bar.value()  # Lazy wrapping may adjust the requested line count.
        for factor in (1.8, 2.4, 1):
            self.edit.set_line_spacing(factor)
            self.settle()
            self.assertEqual(self.edit.firstVisibleBlock().blockNumber(), block)
            self.assertEqual(self.edit.cursorForPosition(QPoint(5, 5)).position(), anchor)
            self.assertEqual(bar.value(), value)
        self.edit.set_line_spacing(1.8)
        self.edit.moveCursor(QTextCursor.MoveOperation.End)
        self.edit.ensureCursorVisible()
        self.settle()
        self.assertTrue(self.edit.viewport().rect().intersects(self.edit.cursorRect()))

    def test_keyboard_vertical_navigation_uses_wrapped_lines(self):
        self.edit.set_line_spacing(1.8)
        self.edit.moveCursor(QTextCursor.MoveOperation.Start)
        self.edit.setFocus()
        block = self.edit.document().firstBlock()
        self.edit.blockBoundingRect(block)
        for index in range(1, 4):
            QTest.keyClick(self.edit, Qt.Key.Key_Down)
            cursor = self.edit.textCursor()
            self.assertEqual(block.layout().lineForTextPosition(cursor.position()).lineNumber(), index)
        for _ in range(3):
            QTest.keyClick(self.edit, Qt.Key.Key_Up)
        self.assertEqual(self.edit.textCursor().position(), 0)

    def test_resize_theme_and_font_keep_draft_and_reapply_pitch(self):
        self.edit.insertPlainText("草稿。")
        before = self.state()
        self.edit.set_line_spacing(1.8)
        for theme, colors in (("light", LIGHT_COLORS), ("dark", DARK_COLORS)):
            apply_theme(self.app, {**DEFAULT_CONFIG, **colors, "theme": theme})
            for width, pixels in ((400, 24), (800, 18)):
                font = self.edit.font()
                font.setPixelSize(pixels)
                self.edit.setFont(font)
                self.edit.resize(width, 420)
                self.settle()
                self.assertEqual(self.edit.palette().color(QPalette.ColorRole.Text).name(), colors["text_color"].lower())
                self.assertEqual(self.state(), before)
                block = self.edit.document().firstBlock()
                self.edit.blockBoundingRect(block)
                line = block.layout().lineAt(0)
                self.assertAlmostEqual(block.layout().lineAt(1).y(), line.height() * 1.8, delta=0.1)
                self.assertEqual(self.edit.horizontalScrollBar().maximum(), 0)

    def test_find_and_extra_selection_survive_spacing(self):
        self.assertTrue(self.edit.find("Mixed text"))
        selected = self.edit.textCursor().selectedText()
        extra = QTextEdit.ExtraSelection()
        extra.cursor = self.edit.textCursor()
        self.edit.setExtraSelections([extra])
        before = self.state()
        self.edit.set_line_spacing(1.8)
        self.settle()
        self.assertEqual(self.state(), before)
        selections = self.edit.extraSelections()  # Keep the owning wrappers alive.
        self.assertEqual(selections[0].cursor.selectedText(), selected)
        self.assertTrue(self.edit.find("Mixed text"))

    def test_ime_spacing_waits_until_commit_without_touching_preedit(self):
        self.edit.setFocus()
        event = QInputMethodEvent("zhongwen", [])
        QApplication.sendEvent(self.edit, event)
        before = self.state()
        self.edit.set_line_spacing(1.8)
        self.settle()
        self.assertEqual(self.state(), before)
        self.assertEqual(self.edit.spacing_layout.factor, 1)
        self.assertEqual(self.edit.document().firstBlock().layout().preeditAreaText(), "zhongwen")
        commit = QInputMethodEvent()
        commit.setCommitString("中文")
        QApplication.sendEvent(self.edit, commit)
        self.settle()
        self.assertEqual(self.edit.spacing_layout.factor, 1.8)
        self.assertTrue(self.edit.toPlainText().startswith("中文"))
        self.edit.undo()
        self.assertEqual(self.edit.toPlainText(), self.source)

    def test_ime_cancel_keeps_source_and_applies_pending_spacing(self):
        QApplication.sendEvent(self.edit, QInputMethodEvent("weishuru", []))
        self.edit.set_line_spacing(1.8)
        QApplication.sendEvent(self.edit, QInputMethodEvent())
        self.settle()
        self.assertEqual(self.edit.toPlainText(), self.source)
        self.assertFalse(self.edit.document().isModified())
        self.assertEqual(self.edit.spacing_layout.factor, 1.8)

    def test_invalid_spacing_does_not_change_state(self):
        before = self.state()
        for factor in (0, -1, 3, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.edit.set_line_spacing(factor)
        self.assertEqual(self.state(), before)

    def test_typing_newline_and_undo_reflow_without_losing_spacing(self):
        self.edit.set_line_spacing(1.8)
        self.edit.moveCursor(QTextCursor.MoveOperation.Start)
        self.edit.insertPlainText("新增一段。\n")
        self.settle()
        block = self.edit.document().firstBlock().next()
        rect = self.edit.blockBoundingRect(block)
        self.assertGreater(rect.height(), block.layout().lineAt(0).height() * 2)
        self.assertAlmostEqual(block.layout().lineAt(1).y(), block.layout().lineAt(0).height() * 1.8, delta=0.1)
        self.edit.undo()
        self.assertEqual(self.edit.toPlainText(), self.source)
        self.edit.redo()
        self.assertTrue(self.edit.toPlainText().startswith("新增一段。\n"))

    def test_unicode_selection_and_empty_document_keep_source(self):
        for source in ("", "\n\n", "中文 🧭 café e\u0301 العربية עברית\n第二段。"):
            self.edit.setPlainText(source)
            self.edit.selectAll()
            before = self.state()
            for factor in (1.8, 2.4, 1):
                self.edit.set_line_spacing(factor)
                self.settle()
                self.assertEqual(self.state(), before)

    def test_real_editor_keeps_serialization_revision_and_dirty_status(self):
        with TemporaryDirectory() as temporary:
            project = NovelProject.create(Path(temporary) / "book", "行距原型")
            project.save_chapter("chapter_01", "找到旧站", self.source, title="旧站")
            path = project.chapters_dir / "chapter_01.md"
            original = path.read_bytes()
            service = DocumentService()
            snapshot = service.open_document(project, "章节", path)
            editor = Editor()
            try:
                editor.resize(1000, 700)
                editor.show()
                editor.load_document(snapshot)
                body = editor.writing_page.body_edit
                self.settle()
                changed = QSignalSpy(editor.dirty_changed)
                context = AIContextSnapshot.capture(project, "chapter_01", editor.document_text(), task_kind="expand")
                for factor in (1.8, 2.4, 1):
                    body.set_line_spacing(factor)
                    self.settle()
                    self.assertEqual(editor.document_text(), snapshot.content)
                    self.assertEqual(editor.loaded_revision(), snapshot.revision)
                    self.assertFalse(editor.is_dirty())
                    self.assertTrue(context.matches(project, "chapter_01", editor.document_text()))
                self.assertEqual(changed.count(), 0)
                self.assertEqual(path.read_bytes(), original)
                body.insertPlainText("作者实际修改。")
                self.assertFalse(context.matches(project, "chapter_01", editor.document_text()))
                self.assertTrue(editor.is_dirty())
            finally:
                editor.close()
                editor.deleteLater()
