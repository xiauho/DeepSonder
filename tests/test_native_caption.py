import ctypes
import os
import unittest
from unittest.mock import Mock
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog, QWidget
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from ui.native_caption import COLOR_DEFAULT, NativeCaptionController, WindowsCaptionBackend, colorref

class NativeCaptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_colorref_channel_order(self):
        self.assertEqual(colorref('#123456'), 0x563412)

    def test_backend_dark_light_inactive_and_accessibility(self):
        backend = object.__new__(WindowsCaptionBackend)
        backend.high_contrast = Mock(return_value=False)
        values = {}
        def record(hwnd, key, pointer, size):
            values[key] = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_uint32)).contents.value
            return -1  # Unsupported attributes must not crash the app.
        backend.set_attribute = record
        backend.apply(123, True, DARK_COLORS, True)
        self.assertEqual(values[20], 1)
        self.assertEqual(values[35], colorref('#181818'))
        self.assertEqual(values[36], colorref(DARK_COLORS['text_color']))
        backend.apply(123, False, LIGHT_COLORS, False)
        self.assertEqual(values[20], 0)
        self.assertEqual(values[35], colorref(LIGHT_COLORS['panel_color']))
        self.assertEqual(values[36], colorref(LIGHT_COLORS['muted_text_color']))
        backend.high_contrast.return_value = True
        backend.apply(123, True, DARK_COLORS, True)
        self.assertEqual(values, {20: 0, 35: COLOR_DEFAULT, 36: COLOR_DEFAULT, 34: COLOR_DEFAULT})

    def test_existing_and_future_dialogs_follow_theme_and_handle_changes(self):
        backend = Mock()
        controller = NativeCaptionController(self.app, backend)
        self.addCleanup(controller.deleteLater)
        self.addCleanup(self.app.removeEventFilter, controller)
        controller.set_theme(True, DARK_COLORS)
        window = QDialog()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.show()
        self.app.processEvents()
        hwnd = int(window.effectiveWinId())
        self.assertTrue(any(call.args[:2] == (hwnd, True) for call in backend.apply.call_args_list))
        backend.reset_mock()
        controller.set_theme(False, LIGHT_COLORS)
        self.assertTrue(any(call.args[:2] == (hwnd, False) for call in backend.apply.call_args_list))
        backend.reset_mock()
        self.app.sendEvent(window, QEvent(QEvent.Type.WinIdChange))
        self.app.processEvents()
        self.assertTrue(backend.apply.called)
        backend.reset_mock()
        child = QWidget(window)
        controller.apply_window(child)
        hidden = QDialog()
        controller.apply_window(hidden)
        self.assertFalse(backend.apply.called)
        hidden.deleteLater()

    def test_destroyed_window_is_safe_with_pending_show_update(self):
        backend = Mock()
        controller = NativeCaptionController(self.app, backend)
        self.addCleanup(controller.deleteLater)
        self.addCleanup(self.app.removeEventFilter, controller)
        controller.set_theme(True, DARK_COLORS)
        window = QDialog()
        window.show()
        import shiboken6
        shiboken6.delete(window)
        self.app.processEvents()

@unittest.skipUnless(os.environ.get('QT_QPA_PLATFORM') == 'windows', 'Requires the native Windows Qt platform')
class NativeCaptionIntegrationTests(unittest.TestCase):
    def test_real_dwm_state_for_main_window_and_quick_dialog(self):
        from ctypes import wintypes
        from PySide6.QtWidgets import QMainWindow
        from ui.quick_access import QuickAccessDialog
        from ui.theme import apply_theme
        app = QApplication.instance() or QApplication([])
        from main import _configure_application
        from PySide6.QtTest import QTest
        _configure_application(app)
        getter = ctypes.WinDLL('dwmapi').DwmGetWindowAttribute
        getter.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        getter.restype = ctypes.c_long
        window = QMainWindow()
        dialog = QuickAccessDialog([], lambda _: '', parent=window)
        try:
            window.show()
            for theme in ('dark', 'light', 'dark'):
                apply_theme(app, {'theme': theme})
                dialog.show()
                QTest.qWait(100)
                for widget in (window, dialog):
                    value = wintypes.DWORD()
                    result = getter(int(widget.winId()), 20, ctypes.byref(value), ctypes.sizeof(value))
                    if result != 0:
                        self.skipTest('This Windows version does not expose the DWM dark-frame attribute')
                    self.assertEqual(value.value, int(theme == 'dark'), (theme, widget.metaObject().className(), app.platformName(), app._native_caption_controller.backend.high_contrast()))
            window.showMaximized()
            app.processEvents()
            self.assertTrue(window.isMaximized())
            window.showNormal()
            app.processEvents()
            self.assertFalse(window.isMaximized())
        finally:
            dialog.close()
            window.close()
            dialog.deleteLater()
            window.deleteLater()
