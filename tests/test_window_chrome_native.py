"""Windows-only integration checks; run with QT_QPA_PLATFORM=windows.

The standard headless suite skips these checks. They create an empty temporary
workbench and never open an existing project or configuration profile.
"""
import ctypes
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from tests.test_window_control_focus import has_focus_border
from core.config import DEFAULT_CONFIG
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from ui.main_window import MainWindow
from ui.window_chrome import HTCAPTION, HTCLIENT, HTMAXBUTTON, HTLEFT, HTTOPLEFT


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("QT_QPA_PLATFORM") == "windows",
                     "Requires an explicit native Windows test surface")
class NativeWindowChromeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.restore = patch.object(MainWindow, "_restore_last_project")
        cls.restore.start()
        cls.window = MainWindow(config={**DEFAULT_CONFIG, "auto_save": False},
                                ui_state_path=Path(cls.tmp.name) / "ui-state.json")
        cls.window.resize(1100, 720)
        cls.window.show()
        cls.app.processEvents()
        cls.frame = cls.window.window_chrome.native_frame
        cls.hwnd = int(cls.window.winId())
        cls.user32 = cls.frame.user32
        from ctypes import wintypes
        cls.user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        cls.user32.SendMessageW.restype = ctypes.c_ssize_t

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.window.deleteLater()
        cls.app.processEvents()
        cls.restore.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.window.showNormal()
        self.window.resize(1100, 720)
        self.window.window_state_controller.activate_route("dashboard")
        self.app.processEvents()

    def physical_point(self, point):
        ratio = self.window.devicePixelRatioF()
        result = self.frame.types.POINT(round(point.x() * ratio), round(point.y() * ratio))
        self.user32.ClientToScreen(self.hwnd, ctypes.byref(result))
        return result

    def hit_test(self, point):
        physical = self.physical_point(point)
        lparam = (physical.x & 0xffff) | ((physical.y & 0xffff) << 16)
        return self.user32.SendMessageW(self.hwnd, 0x0084, 0, lparam)

    def test_native_frame_keeps_capabilities_without_visible_caption(self):
        style = self.frame.get_style(self.hwnd, -16)
        self.assertEqual(style & 0x00CF0000, 0x00CF0000)
        outer, client = self.frame.types.RECT(), self.frame.types.RECT()
        self.user32.GetWindowRect(self.hwnd, ctypes.byref(outer))
        self.user32.GetClientRect.argtypes = [self.frame.types.HWND, ctypes.POINTER(self.frame.types.RECT)]
        self.user32.GetClientRect(self.hwnd, ctypes.byref(client))
        self.assertEqual(outer.bottom - outer.top, client.bottom - client.top)

    def test_native_hit_testing_separates_drag_controls_and_resize(self):
        bar = self.window.title_bar
        self.assertEqual(self.hit_test(QPoint(0, 0)), HTTOPLEFT)
        self.assertEqual(self.hit_test(QPoint(0, 200)), HTLEFT)
        self.assertEqual(self.hit_test(bar.maximize_button.mapTo(self.window, bar.maximize_button.rect().center())), HTMAXBUTTON)
        self.assertEqual(self.hit_test(bar.close_button.mapTo(self.window, bar.close_button.rect().center())), HTCLIENT)
        self.assertEqual(self.hit_test(bar.title.mapTo(self.window, bar.title.rect().center())), HTCAPTION)

    def test_native_large_font_menus_fit_in_both_themes(self):
        bar = self.window.title_bar
        for font in (14, 22):
            for theme, palette in (("light", LIGHT_COLORS), ("dark", DARK_COLORS)):
                with self.subTest(font=font, theme=theme):
                    self.window.appearance_controller.apply({
                        **self.window.config, **palette, "theme": theme, "ui_font_size": font})
                    self.app.processEvents()
                    for action in bar.menu_bar.actions():
                        self.assertTrue(bar.menu_bar.rect().contains(bar.menu_bar.actionGeometry(action)))
                        item_center = bar.menu_bar.mapTo(bar, bar.menu_bar.actionGeometry(action).center())
                        self.assertLessEqual(abs(item_center.y() - bar.brand.geometry().center().y()), 1)
                    self.assertEqual(bar.close_button.geometry().right(), bar.width() - 1)
                    self.assertTrue(bar.rect().contains(bar.minimize_button.geometry()))
                    for button in (bar.minimize_button, bar.maximize_button, bar.close_button):
                        self.assertEqual((button.width(), button.height()), (46, 44))

    def test_native_maximize_respects_work_area_and_restore_state(self):
        normal = self.window.geometry()
        self.window.title_bar.maximize_button.click()
        self.app.processEvents()
        self.assertTrue(self.window.isMaximized())
        self.assertEqual(self.window.title_bar.maximize_button.accessibleName(), "还原")
        self.assertEqual(self.frame.get_style(self.hwnd, -16) & 0x00CF0000, 0x00CF0000)
        info = self.frame.monitor_info(self.hwnd)
        rect = self.frame.types.RECT()
        self.user32.GetWindowRect(self.hwnd, ctypes.byref(rect))
        self.assertEqual((rect.left, rect.top, rect.right, rect.bottom),
                         (info.rcWork.left, info.rcWork.top, info.rcWork.right, info.rcWork.bottom))
        self.assertNotEqual(self.hit_test(QPoint(0, 200)), HTLEFT)
        self.window.title_bar.maximize_button.click()
        self.app.processEvents()
        self.assertFalse(self.window.isMaximized())
        self.assertEqual(self.window.size(), normal.size())
        self.assertEqual(self.frame.get_style(self.hwnd, -16) & 0x00CF0000, 0x00CF0000)

    def test_native_minimize_restore_keeps_frame_and_controls(self):
        self.window.title_bar.minimize_button.click()
        self.app.processEvents()
        self.assertTrue(self.window.isMinimized())
        self.window.showNormal()
        self.app.processEvents()
        self.assertFalse(self.window.isMinimized())
        self.assertTrue(self.window.title_bar.close_button.isVisible())
        self.assertEqual(self.window.title_bar.maximize_button.accessibleName(), "最大化")
        self.assertEqual(self.frame.get_style(self.hwnd, -16) & 0x00CF0000, 0x00CF0000)
        self.assertEqual(self.hit_test(QPoint(0, 200)), HTLEFT)

    def test_native_maximize_mouse_sequence_toggles_only_once(self):
        self.window.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.window, 1000))
        minimize = self.window.title_bar.minimize_button
        self.window.title_bar.maximize_button.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.keyClick(self.window.title_bar.maximize_button, Qt.Key.Key_Backtab)
        self.app.processEvents()
        self.assertIs(self.window.focusWidget(), minimize)
        self.assertTrue(has_focus_border(minimize))
        button = self.window.title_bar.maximize_button
        pos = self.physical_point(button.mapTo(self.window, button.rect().center()))
        old = self.frame.types.POINT()
        self.user32.GetCursorPos(ctypes.byref(old))
        try:
            self.user32.SetCursorPos(pos.x, pos.y)
            self.user32.SendMessageW(self.hwnd, 0x00A1, HTMAXBUTTON, 0)
            self.user32.SendMessageW(self.hwnd, 0x0202, 0, 0)
            self.app.processEvents()
            self.assertTrue(self.window.isMaximized())
            self.assertFalse(button.isDown())
            self.assertFalse(self.frame.max_pressed)
            self.assertFalse(has_focus_border(minimize))
            self.assertFalse(has_focus_border(button))
        finally:
            self.user32.SetCursorPos(old.x, old.y)

    def test_native_mouse_minimize_restore_keeps_editor_focus(self):
        self.window.window_state_controller.activate_route("writing")
        self.app.processEvents()
        self.window.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.window, 1000))
        editor = self.window.editor.text_edit
        editor.setFocus(Qt.FocusReason.OtherFocusReason)
        QTest.mouseClick(self.window.title_bar.minimize_button, Qt.MouseButton.LeftButton)
        self.app.processEvents()
        self.assertTrue(self.window.isMinimized())
        self.window.showNormal()
        self.window.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.window, 1000))
        self.app.processEvents()
        self.assertIs(self.window.focusWidget(), editor)
        self.assertFalse(has_focus_border(self.window.title_bar.minimize_button))

    def test_native_close_command_preserves_review_guard(self):
        with patch.object(self.window, "_can_leave_ai_review", return_value=False):
            self.user32.SendMessageW(self.hwnd, 0x0112, 0xF060, 0)
            self.app.processEvents()
            self.assertTrue(self.window.isVisible())


if __name__ == "__main__":
    unittest.main()
