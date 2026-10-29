"""Reading layout changes must preserve live edits, selections and safety routes."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from core.config import DEFAULT_CONFIG
from core.chapter_document import parse_chapter_document
from core.project import NovelProject
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from ui.main_window import MainWindow
from ui.chapter_writing_page import ChapterWritingPage


class ReadingWorkspaceTests(TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = Path("C:/Windows/Fonts/msyh.ttc")
        cls.font_id = QFontDatabase.addApplicationFont(str(font)) if font.exists() else -1

    @classmethod
    def tearDownClass(cls):
        if cls.font_id >= 0:
            QFontDatabase.removeApplicationFont(cls.font_id)

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.restore = patch.object(MainWindow, "_restore_last_project")
        self.restore.start()
        self.addCleanup(self.restore.stop)
        self.profile = Path(self.tmp.name) / "ui.json"
        self.project = NovelProject.create(Path(self.tmp.name) / "book", "布局测试")
        self.project.save_chapter("chapter_01", "本章目标：找到灯塔", "旧站的灯还亮着。\n\n" * 80, title="第一章 旧站")
        self.path = self.project.chapters_dir / "chapter_01.md"
        self.original = self.path.read_bytes()
        self.windows = []
        self.addCleanup(self.close_windows)
        self.window = self.make_window()

    def make_window(self):
        window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False}, ui_state_path=self.profile)
        self.windows.append(window)
        window.resize(1600, 900)
        window.show()
        window.project_session.set_project(self.project)
        window._show_route("writing")
        window._on_file_selected("章节", str(self.path))
        self.settle()
        return window

    def close_windows(self):
        for window in reversed(self.windows):
            # Tests intentionally leave drafts; never prompt or write them to disk.
            window.editor._set_dirty(False)
            window.close()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def settle(self):
        QTest.qWait(50)
        self.app.processEvents()

    def test_column_toggle_resize_and_theme_preserve_draft_selection_and_undo(self):
        w = self.window
        page = w.editor.writing_page
        edit = page.body_edit
        cursor = edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText("未保存的句子。")
        w.editor._update_stats()
        changed = w.editor.document_text()
        cursor.setPosition(3)
        cursor.setPosition(12, QTextCursor.MoveMode.KeepAnchor)
        edit.setTextCursor(cursor)
        selection = cursor.selectedText()
        undo_steps = edit.document().availableUndoSteps()
        self.assertGreater(page.layout().contentsMargins().left(), 0)
        comfortable = edit.width()
        w.actions["comfortable_width"].trigger()
        self.settle()
        self.assertGreater(edit.width(), comfortable)
        for theme, palette in (("light", LIGHT_COLORS), ("dark", DARK_COLORS)):
            for width in (1100, 1600):
                w.appearance_controller.apply({**w.config, **palette, "theme": theme, "ui_font_size": 20, "editor_font_size": 24})
                w.resize(width, 900)
                w.actions["comfortable_width"].trigger()
                self.settle()
                self.assertIs(page.body_edit, edit)
                self.assertEqual(edit.textCursor().selectedText(), selection)
                self.assertEqual(edit.document().availableUndoSteps(), undo_steps)
                self.assertEqual(w.editor.document_text(), changed)
                self.assertTrue(w.editor.is_dirty())
                self.assertEqual(edit.horizontalScrollBar().maximum(), 0)
        edit.undo()
        self.assertNotIn("未保存的句子。", edit.toPlainText())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_width_preference_survives_restart_without_project_content(self):
        w = self.window
        w.actions["comfortable_width"].trigger()
        w.workspace_state.flush()
        data = json.loads(self.profile.read_text(encoding="utf-8"))
        self.assertIs(data["layout"]["comfortable_width"], False)
        self.assertNotIn("旧站的灯", self.profile.read_text(encoding="utf-8"))
        restored = self.make_window()
        self.assertFalse(restored.actions["comfortable_width"].isChecked())
        self.assertFalse(restored.editor.writing_page.comfortable_width())
        self.assertEqual(restored.editor.writing_page.layout().contentsMargins().left(), 0)
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_toolbar_save_and_reference_reuse_guarded_actions(self):
        w = self.window
        self.assertEqual(w.panel_buttons["inspector"].text(), "参考")
        self.assertIn(w.actions["save"], w.workspace_more_menu.actions())
        self.assertIn(w.actions["comfortable_width"], w.workspace_more_menu.actions())
        self.assertIn(w.actions["locate_document"], w.workspace_more_menu.actions())
        self.assertEqual(w.actions["save"].shortcut().toString(), "Ctrl+S")
        self.assertIn(w.actions["output"], w.ai_creation_menu.actions())
        self.assertFalse(w.editor.body_count_label.isVisible())
        w.panel_buttons["inspector"].click()
        self.settle()
        self.assertTrue(w.inspector.isVisible())
        w.panel_buttons["inspector"].click()
        self.settle()
        for key in ("continuation", "expand", "selection_expand", "style_polish", "style_review", "check", "memory"):
            self.assertIn(w.actions[key], w.ai_creation_menu.actions())
        body = w.editor.writing_page.body_edit
        body.moveCursor(QTextCursor.MoveOperation.End)
        body.insertPlainText("明确保存。")
        w.actions["save"].trigger()
        self.assertFalse(w.editor.is_dirty())
        self.assertIn("明确保存。", self.path.read_text(encoding="utf-8"))

    def test_large_font_toolbar_reflows_without_losing_controls_or_errors(self):
        w = self.window
        w.resize(1100, 720)
        for theme, palette in (("light", LIGHT_COLORS), ("dark", DARK_COLORS)):
            w.appearance_controller.apply({**w.config, **palette, "theme": theme, "ui_font_size": 22})
            self.settle()
            self.assertGreaterEqual(w.editor.width(), 650)
            for button in w.action_bar.findChildren(QToolButton):
                if button.isVisible():
                    self.assertTrue(w.action_bar.rect().contains(button.geometry()))
                    self.assertTrue(button.accessibleName())
            self.assertGreater(w.editor.title_edit.width(), 100)
            w.editor.show_save_error("磁盘写入失败，保留草稿")
            self.settle()
            self.assertTrue(w.editor.path_label.isVisible())
            self.assertIn("保留草稿", w.editor.path_label.text())
            self.assertTrue(w.editor.dirty_badge.isVisible())
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_preparation_summary_refreshes_and_does_not_leak_previous_chapter_details(self):
        page = ChapterWritingPage()
        self.addCleanup(page.close)
        page.load(parse_chapter_document(
            "# 第一章\n\n## 大纲\n- 本章目标：找到灯塔\n- 核心冲突：\n- 章节钩子：\n\n## 正文\n正文。\n"
        ), reset_prep=True)
        self.assertIn("1/3", page.summary.text())
        self.assertIn("目标有内容", page.summary.toolTip())
        page.load(parse_chapter_document(
            "# 第二章\n\n## 大纲\n自由格式的大纲。\n\n## 正文\n正文。\n"
        ), reset_prep=True)
        self.assertNotIn("目标有内容", page.summary.toolTip())
        self.assertIn("大纲已填", page.summary.toolTip())
        page.outline_text.clear()
        self.assertIn("大纲未填", page.summary.text())
        self.assertIn("大纲未填", page.summary.toolTip())

    def test_focus_mode_keeps_live_body_and_reading_preference(self):
        w = self.window
        body = w.editor.writing_page.body_edit
        body.moveCursor(QTextCursor.MoveOperation.End)
        body.insertPlainText("专注模式下保留的草稿。")
        source = w.editor.document_text()
        w.actions["comfortable_width"].trigger()
        for _ in range(2):
            w.actions["focus"].trigger()
            self.settle()
            self.assertTrue(w.window_state_controller.focus_mode)
            self.assertIs(w.focusWidget(), body)
            self.assertFalse(w.editor.writing_page.comfortable_width())
            self.assertEqual(w.editor.document_text(), source)
            w.window_state_controller.exit_focus_mode()
            self.settle()
            self.assertTrue(w.action_bar.isVisible())
            self.assertFalse(w.actions["comfortable_width"].isChecked())
            self.assertEqual(w.editor.document_text(), source)
        self.assertEqual(self.path.read_bytes(), self.original)
