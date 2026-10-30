"""Persist the visible prose, with character anchors rather than scrollbar guesses."""

import json
from pathlib import Path

from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QSignalSpy

from tests import test_prose_width as fixtures
from unittest import TestCase


class ProseReadingPositionTests(TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.ProseWidthTests.setUpClass()
        cls.app = fixtures.ProseWidthTests.app

    @classmethod
    def tearDownClass(cls):
        fixtures.ProseWidthTests.tearDownClass()

    def setUp(self):
        self.fixture = fixtures.ProseWidthTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.window = self.fixture.window
        self.project = self.fixture.project
        self.path = self.fixture.path
        self.second = self.project.chapters_dir / "chapter_02.md"
        prose = ("位置恢复🧭：只存在正文中的内容。" * 14 + "\n\n") * 80
        self.project.save_chapter("chapter_01", "找到灯塔", prose, title="旧站🧭")
        self.project.save_chapter("chapter_02", "", prose, title="次章")
        self.assertTrue(self.window.document_controller.reload_current_file())
        self.fixture.settle()
        self.body = self.window.editor.writing_page.body_edit
        self.original = self.path.read_bytes()
        self.relative = self.path.relative_to(self.project.root).as_posix()

    def place(self, body=None, position=2300, anchor=2200, scroll=100):
        body = body or self.body
        cursor = body.textCursor()
        cursor.setPosition(anchor)
        cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
        body.setTextCursor(cursor)
        body.verticalScrollBar().setValue(scroll)
        self.fixture.settle()
        return body.reading_position()

    def test_capture_uses_real_body_and_contains_no_manuscript(self):
        state = self.place()
        self.window.workspace_state.flush()
        data = json.loads(self.fixture.profile.read_text(encoding="utf-8"))
        stored = data["projects"][str(self.project.root.resolve())]["documents"][self.relative]
        self.assertEqual(stored["prose"], state)
        self.assertNotIn("只存在正文中的内容", self.fixture.profile.read_text(encoding="utf-8"))
        self.assertFalse(self.window.editor.is_dirty())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_restart_restores_selection_direction_and_reading_anchor(self):
        state = self.place(position=2200, anchor=2300)
        self.window.workspace_state.flush()
        self.window.close()
        reopened = self.fixture.make_window()
        body = reopened.editor.writing_page.body_edit
        self.assertEqual(body.reading_position(), state)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_switching_chapters_remembers_each_visible_prose(self):
        first = self.place()
        self.window._on_file_selected("章节", str(self.second))
        self.fixture.settle()
        second = self.place(position=4400, anchor=4500, scroll=180)
        self.window._on_file_selected("章节", str(self.path))
        self.fixture.settle()
        self.assertEqual(self.body.reading_position(), first)
        self.window._on_file_selected("章节", str(self.second))
        self.fixture.settle()
        self.assertEqual(self.body.reading_position(), second)

    def test_font_spacing_width_and_resize_keep_the_reading_anchor_visible(self):
        state = self.place(scroll=150)
        self.window.appearance_controller.apply({**self.window.config, "editor_font_size": 24, "editor_line_spacing": 2.0})
        self.window.editor.writing_page.set_reading_width("custom", 480)
        self.window.resize(1100, 900)
        self.fixture.settle()
        cursor = self.body.textCursor()
        cursor.setPosition(state["top_position"])
        y = self.body.cursorRect(cursor).top()
        self.assertGreaterEqual(y, -self.body.fontMetrics().height())
        self.assertLessEqual(y, self.body.fontMetrics().height() * 2.1)
        self.assertEqual((self.body.textCursor().position(), self.body.textCursor().anchor()), (2300, 2200))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_old_same_document_restore_callback_cannot_override_new_load(self):
        self.place()
        self.window.workspace_state.capture()
        self.assertTrue(self.window.document_controller.reload_current_file())
        cursor = self.body.textCursor()
        cursor.setPosition(5000)
        self.body.setTextCursor(cursor)
        self.body.verticalScrollBar().setValue(200)
        newer = self.body.reading_position()
        self.assertTrue(self.window.document_controller.reload_current_file())
        self.fixture.settle()
        self.assertEqual(self.body.reading_position(), newer)

    def test_repeated_reflow_does_not_accumulate_rounding_drift(self):
        self.body.setPlainText("旧站的灯还亮着。" * 4000)
        self.body.verticalScrollBar().setValue(100)
        self.fixture.settle()
        anchor = self.body.reading_anchor()
        for _ in range(5):
            for size, width in ((24, 480), (18, 900)):
                self.window.appearance_controller.apply({**self.window.config, "editor_font_size": size})
                self.window.editor.writing_page.set_reading_width("custom", width)
                self.fixture.settle()
        cursor = self.body.textCursor()
        cursor.setPosition(anchor)
        self.assertLessEqual(abs(self.body.cursorRect(cursor).top()), self.body.fontMetrics().height() * 2)
        self.assertEqual(self.body.reading_anchor(), anchor)

    def test_legacy_source_offsets_convert_to_prose_utf16_offsets(self):
        self.window.workspace_state.timer.stop()
        source = self.window.editor.document_text()
        from core.chapter_document import parse_chapter_document
        offset = len(source[:parse_chapter_document(source).body_anchor].encode("utf-16-le")) // 2
        data = {"projects": {str(self.project.root.resolve()): {"documents": {self.relative: {
            "cursor": offset + 500, "anchor": offset + 480, "scroll": 0}}}}}
        self.fixture.profile.write_text(json.dumps(data), encoding="utf-8")
        reopened = self.fixture.make_window()
        body = reopened.editor.writing_page.body_edit
        self.assertEqual((body.textCursor().position(), body.textCursor().anchor()), (500, 480))
        self.assertFalse(reopened.editor.is_dirty())

    def test_malformed_and_shortened_document_positions_are_clamped(self):
        state = {"cursor": 10**20, "anchor": -50, "top_position": "invalid"}
        self.body.restore_reading_position(state)
        self.assertEqual(self.body.textCursor().position(), self.body.document().characterCount() - 1)
        self.assertEqual(self.body.textCursor().anchor(), 0)
        self.window.workspace_state.flush()
        self.path.write_text("# 短章\n\n## 正文\n短。", encoding="utf-8")
        self.assertTrue(self.window.document_controller.reload_current_file())
        self.fixture.settle()
        self.assertLessEqual(self.body.textCursor().position(), 2)
        self.assertFalse(self.window.editor.is_dirty())

    def test_view_restore_keeps_dirty_draft_undo_revision_and_save_error(self):
        self.body.insertPlainText("作者未保存的草稿。")
        state = self.place()
        source = self.window.editor.document_text()
        revision = self.window.editor.loaded_revision()
        steps = self.body.document().availableUndoSteps()
        changes = QSignalSpy(self.body.document().contentsChanged)
        saves = QSignalSpy(self.window.editor.file_saved)
        self.window.editor.show_save_error("磁盘只读")
        self.body.restore_reading_position(state)
        self.assertEqual((changes.count(), saves.count()), (0, 0))
        self.assertEqual(self.body.document().availableUndoSteps(), steps)
        self.assertEqual(self.window.editor.document_text(), source)
        self.assertEqual(self.window.editor.loaded_revision(), revision)
        self.assertEqual(self.window.save_status.property("saveState"), "error")
        self.assertTrue(self.window.editor.is_dirty())
        self.assertEqual(self.path.read_bytes(), self.original)
