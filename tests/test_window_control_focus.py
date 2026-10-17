"""Window controls retain editor focus for pointers and show keyboard focus only."""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMainWindow, QPlainTextEdit
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from ui.theme import apply_theme
from ui.workbench_title_bar import WorkbenchTitleBar


def has_focus_border(button):
    image = button.grab().toImage()
    ratio = image.devicePixelRatio()
    return image.pixelColor(round(ratio), round(button.height() * ratio / 2)) == button.palette().highlight().color()


class WindowControlFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        current = QCoreApplication.instance()
        if current is not None and not isinstance(current, QApplication):
            raise unittest.SkipTest("Requires QApplication")
        cls.app = current or QApplication([])

    def setUp(self):
        apply_theme(self.app, {"theme": "light", **LIGHT_COLORS})
        self.window = QMainWindow()
        self.bar = WorkbenchTitleBar(self.window)
        self.window.setMenuWidget(self.bar)
        self.editor = QPlainTextEdit("Temporary text for cursor preservation.")
        self.editor.setTabChangesFocus(True)
        self.window.setCentralWidget(self.editor)
        self.window.setTabOrder(self.editor, self.bar.minimize_button)
        self.window.setTabOrder(self.bar.minimize_button, self.bar.maximize_button)
        self.window.setTabOrder(self.bar.maximize_button, self.bar.close_button)
        self.window.setTabOrder(self.bar.close_button, self.editor)
        self.window.resize(1100, 720)
        self.window.show()
        self.activate()
        self.editor.setFocus(Qt.FocusReason.OtherFocusReason)
        cursor = self.editor.textCursor()
        cursor.setPosition(9)
        self.editor.setTextCursor(cursor)
        self.addCleanup(self.dispose)

    def activate(self):
        self.window.activateWindow()
        self.assertTrue(QTest.qWaitForWindowActive(self.window, 1000))
        self.app.processEvents()

    def dispose(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_mouse_minimize_restore_preserves_editor_focus_and_cursor(self):
        for theme, palette in (("light", LIGHT_COLORS), ("dark", DARK_COLORS)):
            with self.subTest(theme=theme):
                apply_theme(self.app, {"theme": theme, **palette})
                self.editor.setFocus(Qt.FocusReason.OtherFocusReason)
                QTest.mouseClick(self.bar.minimize_button, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                self.assertTrue(self.window.isMinimized())
                self.window.showNormal()
                self.activate()
                self.assertIs(self.window.focusWidget(), self.editor)
                self.assertEqual(self.editor.textCursor().position(), 9)
                self.assertFalse(has_focus_border(self.bar.minimize_button))
                self.assertFalse(self.bar.minimize_button.isDown())

    def test_tab_and_backtab_keep_visible_focus_for_all_controls(self):
        for theme, palette in (("light", LIGHT_COLORS), ("dark", DARK_COLORS)):
            with self.subTest(theme=theme):
                apply_theme(self.app, {"theme": theme, **palette})
                self.editor.setFocus(Qt.FocusReason.OtherFocusReason)
                for button in (self.bar.minimize_button, self.bar.maximize_button, self.bar.close_button):
                    QTest.keyClick(self.window.focusWidget(), Qt.Key.Key_Tab)
                    self.assertIs(self.window.focusWidget(), button)
                    self.assertTrue(has_focus_border(button), button.accessibleName())
                QTest.keyClick(self.bar.close_button, Qt.Key.Key_Backtab)
                self.assertIs(self.window.focusWidget(), self.bar.maximize_button)
                self.assertTrue(has_focus_border(self.bar.maximize_button))

    def test_keyboard_minimize_remains_operable_and_visible_after_restore(self):
        QTest.keyClick(self.editor, Qt.Key.Key_Tab)
        QTest.keyClick(self.bar.minimize_button, Qt.Key.Key_Space)
        self.app.processEvents()
        self.assertTrue(self.window.isMinimized())
        self.window.showNormal()
        self.activate()
        self.assertIs(self.window.focusWidget(), self.bar.minimize_button)
        self.assertTrue(has_focus_border(self.bar.minimize_button))

    def test_mouse_click_clears_existing_keyboard_ring(self):
        QTest.keyClick(self.editor, Qt.Key.Key_Tab)
        self.assertTrue(has_focus_border(self.bar.minimize_button))
        QTest.mouseClick(self.bar.minimize_button, Qt.MouseButton.LeftButton)
        self.window.showNormal()
        self.activate()
        self.assertFalse(has_focus_border(self.bar.minimize_button))
        self.assertFalse(self.bar.minimize_button.isDown())
        # A subsequent keyboard operation restores a visible keyboard target.
        QTest.keyClick(self.bar.minimize_button, Qt.Key.Key_Tab)
        self.assertTrue(has_focus_border(self.bar.maximize_button))

    def test_pointer_on_another_window_control_clears_keyboard_ring(self):
        QTest.keyClick(self.editor, Qt.Key.Key_Tab)
        self.assertTrue(has_focus_border(self.bar.minimize_button))
        QTest.mouseClick(self.bar.maximize_button, Qt.MouseButton.LeftButton)
        self.assertFalse(has_focus_border(self.bar.minimize_button))

    def test_programmatic_focus_does_not_paint_a_keyboard_ring(self):
        for button in (self.bar.minimize_button, self.bar.maximize_button, self.bar.close_button):
            button.setFocus(Qt.FocusReason.OtherFocusReason)
            self.app.processEvents()
            self.assertFalse(has_focus_border(button))

    def test_inactive_window_hides_keyboard_ring(self):
        QTest.keyClick(self.editor, Qt.Key.Key_Tab)
        self.assertTrue(has_focus_border(self.bar.minimize_button))
        other = QMainWindow()
        try:
            other.show()
            other.activateWindow()
            self.assertTrue(QTest.qWaitForWindowActive(other, 1000))
            self.app.processEvents()
            self.assertFalse(self.window.isActiveWindow())
            self.assertFalse(has_focus_border(self.bar.minimize_button))
        finally:
            other.close()
            other.deleteLater()
            self.activate()
        self.assertTrue(has_focus_border(self.bar.minimize_button))


if __name__ == "__main__":
    unittest.main()
