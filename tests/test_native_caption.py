import ctypes
import os
import unittest
from unittest.mock import Mock, patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QApplication, QDialog, QWidget
from core.theme_tokens import DARK_COLORS, LIGHT_COLORS
from ui.native_caption import WM_NCACTIVATE, CAPTION_REDRAW_FLAGS, COLOR_DEFAULT, NativeCaptionController, WindowsCaptionBackend, colorref

class NativeCaptionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_colorref_channel_order(self):
        self.assertEqual(colorref('#123456'), 0x563412)

    def test_backend_dark_light_inactive_and_accessibility(self):
        backend = object.__new__(WindowsCaptionBackend)
        backend.high_contrast = Mock(return_value=False)
        backend.redraw_window = Mock(return_value=True)
        backend.send_message = Mock(return_value=True)
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
        backend.redraw_window.assert_not_called()
        backend.send_message.assert_not_called()

    def test_backend_repaints_once_after_supported_attributes_including_high_contrast(self):
        backend = object.__new__(WindowsCaptionBackend)
        backend.high_contrast = Mock(return_value=False)
        events = []
        def set_attribute(hwnd, attribute, pointer, size):
            events.append(attribute)
            return 0 if attribute == 20 else -1
        backend.set_attribute = set_attribute
        backend.send_message = Mock(side_effect=lambda *args: events.append('activation') or True)
        backend.redraw_window = Mock(side_effect=lambda *args: events.append('paint') or True)
        for high_contrast in (False, True):
            with self.subTest(high_contrast=high_contrast):
                backend.high_contrast.return_value = high_contrast
                events.clear()
                backend.redraw_window.reset_mock()
                backend.send_message.reset_mock()
                backend.apply(123, True, DARK_COLORS, True)
                self.assertEqual(events, [20, 35, 36, 34, 'activation', 'activation', 'paint'])
                backend.redraw_window.assert_called_once_with(123, None, None, CAPTION_REDRAW_FLAGS)
                self.assertEqual(CAPTION_REDRAW_FLAGS, 0x0541)
                self.assertEqual([call.args for call in backend.send_message.call_args_list],
                                 [(123, WM_NCACTIVATE, 0, 0), (123, WM_NCACTIVATE, 1, 0)])

    def test_scheduled_refreshes_are_coalesced(self):
        controller = NativeCaptionController(self.app, Mock())
        self.addCleanup(controller.deleteLater)
        self.addCleanup(self.app.removeEventFilter, controller)
        fired = Mock()
        controller.refresh_timer.timeout.connect(fired)
        for _ in range(10):
            controller.schedule_refresh()
        self.app.processEvents()
        fired.assert_called_once_with()

    def test_frame_refresh_events_do_not_reenter_or_schedule_another_refresh(self):
        backend = Mock()
        controller = NativeCaptionController(self.app, backend)
        self.addCleanup(controller.deleteLater)
        self.addCleanup(self.app.removeEventFilter, controller)
        window = QDialog()
        self.addCleanup(window.deleteLater)
        self.addCleanup(window.close)
        window.show()
        self.app.processEvents()
        from PySide6.QtTest import QTest
        QTest.qWait(50)  # Drain native Show/activation and their deferred refreshes.
        controller.dark, controller.colors = True, dict(DARK_COLORS)
        controller.refresh_timer.stop()
        backend.reset_mock()
        def native_frame_events(*_args):
            controller.schedule_refresh()
            self.app.sendEvent(window, QEvent(QEvent.Type.WindowActivate))
            controller.apply_window(window)
        backend.apply.side_effect = native_frame_events
        controller.apply_window(window)
        self.app.processEvents()
        backend.apply.assert_called_once()
        self.assertFalse(controller.refresh_timer.isActive())
        self.assertFalse(controller._applying_windows)
        self.assertFalse(controller.pending)

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

def _capture_native_caption(hwnd):
    """Print the test window into a private bitmap, including its native frame.

    Unlike desktop screenshots this works without sampling another window.
    The blank caption region is away from the title and window buttons.
    """
    from ctypes import wintypes as W
    user = ctypes.WinDLL('user32')
    gdi = ctypes.WinDLL('gdi32')

    def bind(library, name, arguments, result):
        function = getattr(library, name)
        function.argtypes = arguments
        function.restype = result
        return function

    get_rect = bind(user, 'GetWindowRect', [W.HWND, ctypes.POINTER(W.RECT)], W.BOOL)
    get_dc = bind(user, 'GetWindowDC', [W.HWND], W.HDC)
    release_dc = bind(user, 'ReleaseDC', [W.HWND, W.HDC], ctypes.c_int)
    create_dc = bind(gdi, 'CreateCompatibleDC', [W.HDC], W.HDC)
    create_bitmap = bind(gdi, 'CreateCompatibleBitmap', [W.HDC, ctypes.c_int, ctypes.c_int], W.HBITMAP)
    select = bind(gdi, 'SelectObject', [W.HDC, W.HANDLE], W.HANDLE)
    print_window = bind(user, 'PrintWindow', [W.HWND, W.HDC, W.UINT], W.BOOL)
    get_pixel = bind(gdi, 'GetPixel', [W.HDC, ctypes.c_int, ctypes.c_int], W.DWORD)
    delete_object = bind(gdi, 'DeleteObject', [W.HANDLE], W.BOOL)
    delete_dc = bind(gdi, 'DeleteDC', [W.HDC], W.BOOL)
    bounds = W.RECT()
    if not get_rect(hwnd, ctypes.byref(bounds)):
        raise OSError('GetWindowRect failed')
    source_dc = get_dc(hwnd)
    memory_dc = bitmap = previous = None
    try:
        memory_dc = create_dc(source_dc)
        bitmap = create_bitmap(source_dc, bounds.right - bounds.left, bounds.bottom - bounds.top)
        if not source_dc or not memory_dc or not bitmap:
            raise OSError('Could not allocate native caption capture')
        previous = select(memory_dc, bitmap)
        ok = print_window(hwnd, memory_dc, 2)  # PW_RENDERFULLCONTENT
        return ok, [hex(get_pixel(memory_dc, 350, y)) for y in (10, 20, 30)]
    finally:
        if previous:
            select(memory_dc, previous)
        if bitmap:
            delete_object(bitmap)
        if memory_dc:
            delete_dc(memory_dc)
        if source_dc:
            release_dc(hwnd, source_dc)


@unittest.skipUnless(os.environ.get('QT_QPA_PLATFORM') == 'windows', 'Requires the native Windows Qt platform')
class NativeCaptionIntegrationTests(unittest.TestCase):
    def test_caption_pixels_change_without_resizing(self):
        from PySide6.QtWidgets import QMainWindow
        from PySide6.QtTest import QTest
        from main import _configure_application
        from ui.theme import apply_theme
        app = QApplication.instance() or QApplication([])
        _configure_application(app)
        window = QMainWindow()
        window.setWindowTitle('Caption verification')
        window.resize(700, 400)
        try:
            window.show()
            QTest.qWait(100)
            geometry = window.geometry()
            pixels = []
            for theme in ('dark', 'light', 'dark'):
                apply_theme(app, {'theme': theme})
                if app._native_caption_controller.backend.high_contrast():
                    self.skipTest('High contrast deliberately uses system caption colors')
                QTest.qWait(100)
                ok, colors = _capture_native_caption(int(window.winId()))
                self.assertTrue(ok, 'PrintWindow failed to capture the native frame')
                self.assertNotIn('0xffffffff', colors, 'GetPixel failed')
                pixels.append(colors)
                self.assertEqual(window.geometry(), geometry)
            self.assertNotEqual(pixels[0], pixels[1], pixels)
            self.assertEqual(pixels[0], pixels[2], pixels)
        finally:
            window.close()
            window.deleteLater()

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
            dialog.show()
            QTest.qWait(100)
            apply_theme(app, {'theme': 'light'})
            backend = app._native_caption_controller.backend
            with patch.object(backend, 'redraw_window', wraps=backend.redraw_window) as redraw, patch.object(backend, 'send_message', wraps=backend.send_message) as activation:
                for theme in ('dark', 'light', 'dark'):
                    before = [(widget.geometry(), widget.windowState()) for widget in (window, dialog)]
                    active = app.activeWindow()
                    redraw.reset_mock()
                    activation.reset_mock()
                    apply_theme(app, {'theme': theme})
                    for widget in (window, dialog):
                        self.assertTrue(any(call.args == (int(widget.winId()), None, None, CAPTION_REDRAW_FLAGS)
                                            for call in redraw.call_args_list))
                    for widget in (window, dialog):
                        self.assertTrue(any(call.args == (int(widget.winId()), WM_NCACTIVATE, int(widget.isActiveWindow()), 0)
                                            for call in activation.call_args_list))
                    self.assertEqual(before, [(widget.geometry(), widget.windowState()) for widget in (window, dialog)])
                    self.assertIs(app.activeWindow(), active)
                    QTest.qWait(100)
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
                    colors = DARK_COLORS if theme == 'dark' else LIGHT_COLORS
                    expected = {
                        35: colorref(colors['background_color'] if theme == 'dark' else colors['panel_color']),
                        36: colorref(colors['text_color'] if widget.isActiveWindow() else colors['muted_text_color']),
                        34: colorref(colors['border_color']),
                    }
                    for attribute, color in expected.items():
                        if getter(int(widget.winId()), attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                            self.assertEqual(value.value, COLOR_DEFAULT if backend.high_contrast() else color)

            window.showMaximized()
            app.processEvents()
            self.assertTrue(window.isMaximized())
            apply_theme(app, {'theme': 'light'})
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
