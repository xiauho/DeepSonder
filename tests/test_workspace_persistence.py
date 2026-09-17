import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QRect
from PySide6.QtWidgets import QApplication
from core.config import DEFAULT_CONFIG
from core.project import NovelProject
from ui.main_window import MainWindow
from ui.workspace_state_controller import fit_geometry


class WorkspacePersistenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        instance = QCoreApplication.instance()
        if instance is not None and not isinstance(instance, QApplication):
            raise unittest.SkipTest("Requires QApplication")
        cls.app = instance or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = NovelProject.create(self.root / "novel", "恢复测试")
        self.first = self.project.chapters_dir / "chapter_01.md"
        self.second = self.project.chapters_dir / "chapter_02.md"
        self.first.write_text("# 首章\n\n## 正文\n" + "写作位置测试。\n" * 240, encoding="utf-8")
        self.second.write_text("# 次章\n\n## 正文\n另一段正文。", encoding="utf-8")
        self.state = self.root / "ui-state.json"
        patcher = patch.object(MainWindow, "_restore_last_project")
        patcher.start()
        self.addCleanup(patcher.stop)

    def window(self):
        window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False}, ui_state_path=self.state)
        window.project_lifecycle_controller.persist_config = lambda _config: None
        window.resize(1300, 800)
        window.show()
        self.addCleanup(window.close)
        self.assertTrue(window._load_project(self.project.root, quiet=True))
        self.events()
        return window

    def events(self):
        for _ in range(3):
            self.app.processEvents()

    def test_restart_restores_document_selection_scroll_and_panel_preferences(self):
        window = self.window()
        cursor = window.editor.text_edit.textCursor()
        cursor.setPosition(200)
        window.editor.text_edit.setTextCursor(cursor)
        window.editor.text_edit.verticalScrollBar().setValue(60)
        expected_scroll = window.editor.text_edit.verticalScrollBar().value()
        window.window_state_controller.toggle_inspector()
        self.events()
        window.workspace_state.flush()
        window.close()
        reopened = self.window()
        self.assertEqual(Path(reopened.editor.current_path()), self.first)
        self.assertEqual(reopened.editor.text_edit.textCursor().position(), 200)
        self.assertEqual(reopened.editor.text_edit.verticalScrollBar().value(), expected_scroll)
        self.assertTrue(reopened.window_state_controller.inspector_visible)
        self.assertNotIn("写作位置测试", self.state.read_text(encoding="utf-8"))

    def test_switch_document_restores_each_position_and_last_document(self):
        window = self.window()
        cursor = window.editor.text_edit.textCursor()
        cursor.setPosition(45)
        window.editor.text_edit.setTextCursor(cursor)
        window.left_panel.select_path(self.second)
        self.events()
        window.left_panel.select_path(self.first)
        self.events()
        self.assertEqual(window.editor.text_edit.textCursor().position(), 45)
        window.left_panel.select_path(self.second)
        self.events()
        window.close()
        reopened = self.window()
        self.assertEqual(Path(reopened.editor.current_path()), self.second)

    def test_missing_last_chapter_falls_back_and_shorter_document_clamps_cursor(self):
        window = self.window()
        window.left_panel.select_path(self.second)
        self.events()
        window.close()
        self.second.unlink()
        reopened = self.window()
        self.assertEqual(Path(reopened.editor.current_path()), self.first)
        cursor = reopened.editor.text_edit.textCursor()
        cursor.setPosition(1000)
        reopened.editor.text_edit.setTextCursor(cursor)
        reopened.close()
        self.first.write_text("# 短章", encoding="utf-8")
        shortened = self.window()
        self.assertLessEqual(shortened.editor.text_edit.textCursor().position(), 4)

    def test_corrupt_state_and_outside_project_path_are_ignored(self):
        self.state.write_text("{broken", encoding="utf-8")
        window = self.window()
        self.assertEqual(Path(window.editor.current_path()), self.first)
        window.workspace_state.data["projects"][str(self.project.root)] = {"last": "../secret.md"}
        (self.root / "secret.md").write_text("private", encoding="utf-8")
        self.assertIsNone(window.workspace_state.last_document(self.project))

    def test_main_window_save_uses_real_service_and_failure_survives_typing(self):
        window = self.window()
        window.editor.text_edit.insertPlainText("新内容")
        with patch.object(window.document_controller.document_service, "save_document", side_effect=OSError("磁盘只读")):
            self.assertFalse(window.document_controller.save())
        window.editor.text_edit.insertPlainText("继续写")
        self.assertEqual(window.editor.dirty_badge.text(), "保存失败")
        self.assertFalse(window.editor.path_label.isHidden())
        self.assertTrue(window.document_controller.save())
        self.assertEqual(window.editor.dirty_badge.text(), "已保存")
        self.assertTrue(window.editor.path_label.isHidden())
        self.assertIn("新内容", self.first.read_text(encoding="utf-8"))

    def test_clear_search_and_locate_current_document(self):
        window = self.window()
        window.left_panel.search.setText("不存在的关键词")
        self.assertTrue(window.left_panel.clear_search_button.isVisible())
        window.left_panel.clear_search_button.click()
        self.assertEqual(window.left_panel.search.text(), "")
        window.left_panel.search.setText("不存在")
        window.left_panel.locate_button.click()
        self.assertEqual(window.left_panel.search.text(), "")
        self.assertEqual(window.left_panel.tree.currentItem().data(0, window.left_panel.PATH_ROLE), str(self.first))

    def test_ai_unavailable_menu_explains_and_offers_resolution(self):
        window = self.window()
        window.editor.clear_document()
        window._refresh_ai_actions()
        self.assertIn("选择一个章节", window.ai_availability_hint.text())
        self.assertEqual(window.ai_availability_action.text(), "选择章节")
        self.assertFalse(window.actions["expand"].isEnabled())
        window.left_panel.select_path(self.first)
        with patch.object(window.ai_controller, "is_running", return_value=True):
            window._refresh_ai_actions()
            self.assertIn("正在进行", window.actions["expand"].toolTip())
            self.assertEqual(window.ai_availability_action.text(), "查看 AI 任务")
        window._refresh_ai_actions()

    def test_project_switch_keeps_independent_bookmarks(self):
        window = self.window()
        cursor = window.editor.text_edit.textCursor()
        cursor.setPosition(120)
        window.editor.text_edit.setTextCursor(cursor)
        other = NovelProject.create(self.root / "other", "另一个项目")
        self.assertTrue(window._load_project(other.root, quiet=True))
        self.events()
        self.assertTrue(window._load_project(self.project.root, quiet=True))
        self.events()
        self.assertEqual(window.editor.text_edit.textCursor().position(), 120)

    def test_focus_mode_does_not_persist_hidden_navigation_as_preference(self):
        window = self.window()
        window.window_state_controller.toggle_focus_mode()
        window.workspace_state.flush()
        self.assertTrue(json.loads(self.state.read_text(encoding="utf-8"))["layout"]["navigation_visible"])
        with patch("ui.workspace_state_controller.atomic_write_text", side_effect=OSError("read only")):
            window.workspace_state.flush()
        self.assertIn("界面偏好保存失败", window.status_message.text())

    def test_saved_geometry_recovers_from_removed_monitor(self):
        screen = QRect(0, 0, 1920, 1080)
        recovered = fit_geometry([8000, -8000, 4000, 3000], [screen])
        self.assertTrue(screen.contains(recovered))
        self.assertTrue(screen.contains(fit_geometry([10**100, 0, 1200, 800], [screen])))
        left_screen = QRect(-1920, 0, 1920, 1080)
        recovered = fit_geometry([-1800, 30, 1200, 800], [screen, left_screen])
        self.assertTrue(left_screen.contains(recovered))
        self.assertTrue(QRect(0, 0, 1024, 768).contains(fit_geometry(None, [QRect(0, 0, 1024, 768)])))
