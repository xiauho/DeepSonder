"""Exercise grouped navigation and compact tasks through the real guarded routes."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtGui import QFontDatabase, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QToolButton

from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from core.task_controller import AITaskToken
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from tests.test_canon_schema import character_text
from ui.main_window import MainWindow


class GroupedNavigationTests(TestCase):
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
        restore = patch.object(MainWindow, "_restore_last_project")
        restore.start()
        self.addCleanup(restore.stop)
        self.project = NovelProject.create(Path(self.tmp.name) / "book", "分组导航测试")
        self.project.save_chapter("chapter_01", "先见到灯塔", "第一章正文。\n\n" * 30, title="第1章 归来")
        self.project.save_chapter("chapter_02", "再找到车票", "第二章正文。\n\n" * 30, title="第2章 车票")
        self.card = self.project.canon_dir / "characters" / "白鸥.md"
        self.card.write_text(character_text(), encoding="utf-8")
        self.originals = {p: p.read_bytes() for p in [*self.project.list_chapters(), self.card]}
        self.window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False}, ui_state_path=Path(self.tmp.name) / "ui.json")
        self.addCleanup(self.close_window)
        self.window.resize(1100, 720)
        self.window.show()
        self.window.project_session.set_project(self.project)
        self.window._show_route("writing")
        self.settle()

    def close_window(self):
        w = self.window
        w.ai_workflow_controller.discard_pending_result()
        w.editor._set_dirty(False)
        w.close()
        w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def settle(self):
        QTest.qWait(80)
        self.app.processEvents()

    def assert_source_unchanged(self):
        for path, data in self.originals.items():
            self.assertEqual(path.read_bytes(), data)

    def test_project_and_more_menus_reuse_commands_and_are_available_from_every_page(self):
        w = self.window
        self.assertEqual(set(w.primary_nav.ROUTES[i][0] for i in range(len(w.primary_nav.ROUTES))), {"writing", "canon", "reports"})
        for key in ("dashboard", "new_project", "open_project", "import_legacy_project", "export"):
            self.assertIn(w.actions[key], w.primary_nav.project_menu.actions())
        self.assertIn(w.actions["trash"], w.primary_nav.more_menu.actions())
        self.assertIn(w.actions["commands"], w.primary_nav.more_menu.actions())
        for key in ("dashboard", "export", "show_memory", "show_reports"):
            w.actions[key].trigger()
            self.settle()
            self.assertTrue(w.primary_nav.project_button.isVisible())
            self.assertTrue(w.primary_nav.more_button.isVisible())
        commands = {entry.key: entry for entry in w.quick_access_controller.entries() if entry.mode == "commands"}
        self.assertIn("show_memory", commands)
        self.assertIn("show_reports", commands)
        self.assert_source_unchanged()

    def test_workspace_switch_restores_chapter_planning_and_each_search(self):
        w = self.window
        chapter = self.project.chapters_dir / "chapter_02.md"
        w._on_file_selected("章节", str(chapter))
        w.left_panel.search.setText("第2")
        w.primary_nav.buttons["canon"].click()
        self.settle()
        self.assertEqual(w.window_state_controller.current_route, "canon")
        self.assertEqual(w.editor.current_path(), str(self.project.outline_dir / "story_plan.json"))
        self.assertEqual(w.left_panel.scope_title.text(), "故事资料")
        w.left_panel.search.setText("不存在的资料")
        w.primary_nav.buttons["writing"].click()
        self.settle()
        self.assertEqual(w.editor.current_path(), str(chapter))
        self.assertIn("第二章正文", w.editor.writing_page.body_edit.toPlainText())
        self.assertEqual(w.left_panel.search.text(), "第2")
        self.assertEqual(w.left_panel.scope_title.text(), "章节")
        w.primary_nav.buttons["canon"].click()
        self.settle()
        self.assertTrue(w.editor.is_story_plan())
        self.assertEqual(w.left_panel.search.text(), "不存在的资料")
        self.assert_source_unchanged()

    def test_workspace_switch_restores_structured_material_and_timeline(self):
        w = self.window
        w._on_file_selected("角色", str(self.card))
        w.left_panel.search.setText("白鸥")
        w.primary_nav.buttons["writing"].click()
        self.settle()
        self.assertTrue(w.editor.is_writing_page_visible())
        w.primary_nav.buttons["canon"].click()
        self.settle()
        self.assertEqual(w.structured_canon_page.current_path(), self.card)
        self.assertTrue(w.structured_canon_page.isVisible())
        self.assertEqual(w.left_panel.search.text(), "白鸥")
        w.manage_timeline()
        w.left_panel.search.setText("时间")
        w.primary_nav.buttons["writing"].click()
        self.settle()
        w.primary_nav.buttons["canon"].click()
        self.settle()
        self.assertEqual(w.window_state_controller.current_route, "timeline")
        self.assertTrue(w.timeline_page.isVisible())
        self.assertEqual(w.left_panel.search.text(), "时间")
        self.assert_source_unchanged()

    def test_review_group_keeps_distinct_pages_and_last_selected_tab(self):
        w = self.window
        w.primary_nav.buttons["reports"].click()
        self.settle()
        self.assertTrue(w.reports_page.isVisible())
        self.assertTrue(w.review_tabs.isVisible())
        w.review_buttons["memory"].click()
        self.settle()
        self.assertTrue(w.memory_page.isVisible())
        self.assertTrue(w.review_buttons["memory"].isChecked())
        self.assertFalse(w.review_buttons["reports"].isChecked())
        self.assertEqual(w.primary_nav.buttons["reports"].objectName(), "primaryNavActive")
        w.primary_nav.buttons["writing"].click()
        self.settle()
        self.assertFalse(w.review_tabs.isVisible())
        w.primary_nav.buttons["reports"].click()
        self.settle()
        self.assertEqual(w.window_state_controller.current_route, "memory")
        w.actions["show_reports"].trigger()
        self.settle()
        self.assertTrue(w.reports_page.isVisible())
        self.assert_source_unchanged()

    def test_rejected_switch_retains_draft_route_and_navigation_highlight(self):
        w = self.window
        edit = w.editor.writing_page.body_edit
        edit.moveCursor(QTextCursor.MoveOperation.End)
        edit.insertPlainText("尚未保存的内容。")
        source = w.editor.document_text()
        with patch.object(w, "_save_if_dirty", return_value=False):
            w.primary_nav.buttons["canon"].click()
            w.actions["show_memory"].trigger()
        self.assertEqual(w.window_state_controller.current_route, "writing")
        self.assertEqual(w.primary_nav.buttons["writing"].objectName(), "primaryNavActive")
        self.assertEqual(w.editor.document_text(), source)
        self.assertTrue(w.editor.is_dirty())
        self.assert_source_unchanged()

    def test_compact_task_details_and_logs_preserve_pending_result(self):
        w = self.window
        token = AITaskToken("expand", "chapter_01")
        w.task_panel.start(token)
        w.window_state_controller.show_output()
        self.settle()
        compact = w.task_panel.height()
        self.assertLess(compact, 140)
        self.assertTrue(w.task_panel.cancel_button.isVisible())
        self.assertTrue(w.task_panel.detail.isVisible())
        w.ai_task_view_controller.append_output("本次工作记录")
        w.task_panel.details_toggle.click()
        w.task_panel.log_toggle.click()
        self.settle()
        self.assertGreater(w.task_panel.height(), compact)
        self.assertTrue(w.task_panel.log_container.isVisible())
        w.ai_workflow_controller._on_expansion_done = Mock()
        w.ai_workflow_controller._on_task_succeeded(token, "candidate")
        w.task_panel.details_toggle.click()
        self.settle()
        self.assertFalse(w.task_panel.log_container.isVisible())
        self.assertIn("本次工作记录", w.output_panel.toPlainText())
        self.assertTrue(w.task_panel.review_button.isVisible())
        w.primary_nav.buttons["reports"].click()
        w.primary_nav.buttons["writing"].click()
        self.settle()
        self.assertTrue(w.ai_workflow_controller.has_pending_result)
        self.assertEqual(w.task_panel.state, "pending")
        self.assert_source_unchanged()
        w.task_panel.review_button.click()
        w.ai_workflow_controller._on_expansion_done.assert_called_once_with(token, "candidate")

    def test_large_font_review_pages_and_task_actions_fit_in_both_themes(self):
        w = self.window
        token = AITaskToken("expand", "chapter_" + "long_name" * 30)
        for theme, palette in (("light", LIGHT_COLORS), ("dark", DARK_COLORS)):
            w.appearance_controller.apply({**w.config, **palette, "theme": theme, "ui_font_size": 22})
            w.resize(1100, 720)
            w.actions["show_memory"].trigger()
            self.settle()
            self.assertEqual(w.width(), 1100)
            for button in w.review_buttons.values():
                self.assertTrue(w.review_tabs.rect().contains(button.geometry()))
            w.primary_nav.buttons["writing"].click()
            w.task_panel.pending(token)
            w.window_state_controller.show_output()
            self.settle()
            self.assertEqual(w.width(), 1100)
            for button in (w.task_panel.review_button, w.task_panel.discard_button, w.task_panel.details_toggle):
                self.assertTrue(w.task_panel.rect().contains(button.geometry()))
                self.assertTrue(button.isVisible())
            self.assertGreaterEqual(w.task_panel.detail.height(), w.task_panel.detail.heightForWidth(w.task_panel.detail.width()))
        self.assert_source_unchanged()
