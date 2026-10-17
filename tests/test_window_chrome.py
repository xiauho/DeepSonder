import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox
from core.config import DEFAULT_CONFIG
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from ui.main_window import MainWindow
from ui.window_chrome import (
    HTLEFT, HTRIGHT, HTTOP, HTBOTTOM, HTTOPLEFT, HTTOPRIGHT,
    HTBOTTOMLEFT, HTBOTTOMRIGHT, resize_hit_test,
)


class ResizeHitTests(unittest.TestCase):
    def test_eight_edges_at_multiple_display_scales(self):
        for scale in (1, 1.5, 2):
            width, height, border = round(1100 * scale), round(720 * scale), round(6 * scale)
            points = (
                (0, 0, HTTOPLEFT), (width // 2, 0, HTTOP),
                (width - 1, 0, HTTOPRIGHT), (0, height // 2, HTLEFT),
                (width - 1, height // 2, HTRIGHT), (0, height - 1, HTBOTTOMLEFT),
                (width // 2, height - 1, HTBOTTOM), (width - 1, height - 1, HTBOTTOMRIGHT),
            )
            for x, y, expected in points:
                with self.subTest(scale=scale, pos=(x, y)):
                    self.assertEqual(resize_hit_test(x, y, width, height, border), expected)
                    self.assertIsNone(resize_hit_test(x, y, width, height, border, maximized=True))
            self.assertIsNone(resize_hit_test(border, border, width, height, border))
            self.assertIsNone(resize_hit_test(-1, 0, width, height, border))
            self.assertIsNone(resize_hit_test(width, height, width, height, border))


class WorkbenchWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        instance = QCoreApplication.instance()
        if instance is not None and not isinstance(instance, QApplication):
            raise unittest.SkipTest("Requires QApplication")
        cls.app = instance or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "ui-state.json"
        restore = patch.object(MainWindow, "_restore_last_project")
        restore.start()
        self.addCleanup(restore.stop)
        self.window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False}, ui_state_path=self.state)
        self.window.resize(1100, 720)
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.dispose)

    def dispose(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_all_routes_and_focus_keep_controls_and_menu_actions(self):
        window = self.window
        bar = window.title_bar
        self.assertTrue(window.windowFlags() & Qt.WindowType.FramelessWindowHint)
        self.assertIs(window.menuWidget(), bar)
        self.assertIs(window.menuBar(), bar.menu_bar)
        self.assertEqual([a.text() for a in window.menuBar().actions()],
                         ["文件", "编辑", "创作", "视图", "帮助"])
        for theme in ("light", "dark"):
            window.appearance_controller.apply({**window.config, **(DARK_COLORS if theme == "dark" else LIGHT_COLORS), "theme": theme})
            for route in window.ROUTES:
                with self.subTest(theme=theme, route=route):
                    window.window_state_controller.activate_route(route)
                    self.app.processEvents()
                    for button in (bar.minimize_button, bar.maximize_button, bar.close_button):
                        self.assertTrue(button.isVisible())
                        self.assertTrue(bar.rect().contains(button.geometry()))
                        self.assertTrue(button.accessibleName())
                        self.assertEqual((button.width(), button.height()), (46, 44))
                    self.assertEqual(bar.close_button.geometry().right(), bar.width() - 1)
                    for action in bar.menu_bar.actions():
                        item_center = bar.menu_bar.mapTo(bar, bar.menu_bar.actionGeometry(action).center())
                        self.assertLessEqual(abs(item_center.y() - bar.brand.geometry().center().y()), 1)
            window.window_state_controller.activate_route("writing")
            window.toggle_focus_mode()
            self.app.processEvents()
            self.assertTrue(bar.isVisible())
            self.assertTrue(bar.close_button.isVisible())
            self.assertFalse(bar.menu_bar.isVisible())
            self.assertTrue(bar.exit_focus_button.isVisible())
            self.assertFalse(window.editor.exit_focus_button.isVisible())
            QTest.mouseClick(bar.exit_focus_button, Qt.MouseButton.LeftButton)
            self.assertFalse(window.window_state_controller.focus_mode)
            self.assertTrue(bar.menu_bar.isVisible())
            self.assertFalse(bar.exit_focus_button.isVisible())

    def test_maximize_restore_minimize_and_geometry_persistence(self):
        window = self.window
        bar = window.title_bar
        normal = window.geometry()
        QTest.mouseClick(bar.maximize_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(window.isMaximized())
        self.assertEqual(bar.maximize_button.accessibleName(), "还原")
        window.workspace_state.flush()
        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertTrue(saved["maximized"])
        self.assertEqual(saved["geometry"], [normal.x(), normal.y(), normal.width(), normal.height()])
        QTest.mouseClick(bar.maximize_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertFalse(window.isMaximized())
        self.assertEqual(bar.maximize_button.accessibleName(), "最大化")
        self.assertEqual(window.geometry().size(), normal.size())
        QTest.mouseClick(bar.minimize_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(window.isMinimized())
        window.workspace_state.flush()
        self.assertEqual(json.loads(self.state.read_text(encoding="utf-8"))["geometry"], saved["geometry"])
        window.showNormal()
        self.app.processEvents()

    def test_saved_maximized_state_reopens_with_restore_control(self):
        self.window.showMaximized()
        self.app.processEvents()
        self.window.workspace_state.flush()
        reopened = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False}, ui_state_path=self.state)
        try:
            reopened.workspace_state.restore_geometry()
            reopened.show()
            self.app.processEvents()
            self.assertTrue(reopened.isMaximized())
            self.assertEqual(reopened.title_bar.maximize_button.accessibleName(), "还原")
            reopened.title_bar.maximize_button.click()
            self.app.processEvents()
            self.assertFalse(reopened.isMaximized())
            # The headless screen can be narrower than the saved desktop.
            # Restoration must keep the entire frameless window reachable.
            self.assertTrue(reopened.screen().availableGeometry().contains(reopened.geometry()))
            self.assertTrue(reopened.title_bar.close_button.isVisible())
        finally:
            reopened.close()
            reopened.deleteLater()
            self.app.processEvents()

    def test_long_project_title_never_displaces_controls_or_drag_region(self):
        window = self.window
        bar = window.title_bar
        window.setWindowTitle("DeepSonder-PySide6 — " + "长篇项目名称" * 80)
        self.app.processEvents()
        self.assertLess(bar.title.width(), window.width() - 3 * 46)
        self.assertLess(len(bar.title.text()), 400)
        self.assertNotIn("DeepSonder-PySide6", bar.title.text())
        self.assertTrue(bar.is_drag_position(bar.title.geometry().center()))
        self.assertFalse(bar.is_drag_position(bar.close_button.geometry().center()))
        self.assertFalse(bar.is_drag_position(bar.menu_bar.geometry().center()))
        self.assertFalse(bar.is_drag_position(QPoint(-1, -1)))
        self.assertEqual(bar.close_button.geometry().right(), window.width() - 1)

    def test_top_blank_double_click_toggles_maximize(self):
        bar = self.window.title_bar
        QTest.mouseDClick(bar, Qt.MouseButton.LeftButton, pos=bar.title.geometry().center())
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())

    def test_close_button_respects_pending_review(self):
        with patch.object(self.window, "_can_leave_ai_review", return_value=False), \
                patch.object(self.window.ai_engine_controller, "cleanup") as cleanup:
            self.window.title_bar.close_button.click()
            self.assertTrue(self.window.isVisible())
            cleanup.assert_not_called()

    def test_close_button_respects_save_failure(self):
        with patch.object(self.window.editor, "is_dirty", return_value=True), \
                patch.object(self.window, "save_current_file", return_value=False), \
                patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.No), \
                patch.object(self.window.ai_engine_controller, "cleanup") as cleanup:
            self.window.title_bar.close_button.click()
            self.assertTrue(self.window.isVisible())
            cleanup.assert_not_called()

    def test_close_button_respects_running_ai(self):
        with patch.object(self.window.ai_controller, "is_running", return_value=True), \
                patch("ui.main_window.QMessageBox.information") as info:
            self.window.title_bar.close_button.click()
            self.assertTrue(self.window.isVisible())
            info.assert_called_once()

    def test_close_button_respects_unsaved_settings(self):
        with patch.object(self.window.settings_page, "has_unsaved_changes", return_value=True), \
                patch("ui.main_window.QMessageBox.question", return_value=QMessageBox.StandardButton.Cancel):
            self.window.title_bar.close_button.click()
            self.assertTrue(self.window.isVisible())

    def test_close_button_flushes_state_before_cleanup(self):
        with patch.object(self.window.workspace_state, "flush") as flush, \
                patch.object(self.window.ai_engine_controller, "cleanup") as cleanup:
            self.window.title_bar.close_button.click()
            self.assertFalse(self.window.isVisible())
            flush.assert_called_once()
            cleanup.assert_called_once()


if __name__ == "__main__":
    unittest.main()
