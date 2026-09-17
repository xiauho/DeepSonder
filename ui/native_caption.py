"""Synchronise Qt top-level windows with the Windows DWM caption theme."""
from __future__ import annotations

import ctypes
import sys
import weakref
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

COLOR_DEFAULT = 0xFFFFFFFF
# RDW_INVALIDATE | RDW_FRAME | RDW_UPDATENOW | RDW_NOCHILDREN.
# Repaint the native frame immediately without invalidating child widgets.
CAPTION_REDRAW_FLAGS = 0x0001 | 0x0400 | 0x0100 | 0x0040
WM_NCACTIVATE = 0x0086


def colorref(value: str) -> int:
    """DWM uses COLORREF (0x00BBGGRR), not RGB integers."""
    return int(value[1:3], 16) | int(value[3:5], 16) << 8 | int(value[5:7], 16) << 16


class WindowsCaptionBackend:
    def __init__(self):
        self.dwm = ctypes.WinDLL('dwmapi', use_last_error=True)
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.set_attribute = self.dwm.DwmSetWindowAttribute
        self.set_attribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        self.set_attribute.restype = ctypes.c_long
        self.send_message = self.user.SendMessageW
        self.send_message.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.send_message.restype = ctypes.c_ssize_t
        self.redraw_window = self.user.RedrawWindow
        self.redraw_window.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.HRGN, wintypes.UINT]
        self.redraw_window.restype = wintypes.BOOL
        self.user.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
        self.user.SystemParametersInfoW.restype = wintypes.BOOL

    def high_contrast(self) -> bool:
        class HIGHCONTRAST(ctypes.Structure):
            _fields_ = [('cbSize', wintypes.UINT), ('dwFlags', wintypes.DWORD), ('scheme', wintypes.LPWSTR)]
        state = HIGHCONTRAST()
        state.cbSize = ctypes.sizeof(state)
        # If the accessibility preference cannot be read, keep system colours.
        ok = self.user.SystemParametersInfoW(0x0042, state.cbSize, ctypes.byref(state), 0)
        return not ok or bool(state.dwFlags & 1)

    def apply(self, hwnd: int, dark: bool, colors: dict, active: bool) -> None:
        system_colors = self.high_contrast()
        values = {
            20: int(dark and not system_colors),  # DWMWA_USE_IMMERSIVE_DARK_MODE
            35: COLOR_DEFAULT if system_colors else colorref(colors['background_color'] if dark else colors['panel_color']),
            36: COLOR_DEFAULT if system_colors else colorref(colors['text_color'] if active else colors['muted_text_color']),
            34: COLOR_DEFAULT if system_colors else colorref(colors['border_color']),
        }
        applied = False
        for attribute, value in values.items():
            payload = wintypes.DWORD(value)
            # Unsupported attributes return a failed HRESULT on older Windows;
            # retain native system rendering rather than changing window flags.
            result = self.set_attribute(hwnd, attribute, ctypes.byref(payload), ctypes.sizeof(payload))
            applied = result >= 0 or applied
        if applied:
            # Windows 10 caches the caption's dark-mode appearance even after
            # frame recalculation and WM_NCPAINT. Refresh its non-client active
            # appearance, then restore the real state. WM_NCACTIVATE paints the
            # caption; it does not activate the window or move keyboard focus.
            self.send_message(hwnd, WM_NCACTIVATE, int(not active), 0)
            self.send_message(hwnd, WM_NCACTIVATE, int(active), 0)
            self.redraw_window(hwnd, None, None, CAPTION_REDRAW_FLAGS)


class _SystemThemeEvents(QAbstractNativeEventFilter):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) in (b'windows_generic_MSG', b'windows_dispatcher_MSG'):
            msg = wintypes.MSG.from_address(int(message))
            if msg.message in (0x001A, 0x031A, 0x0086, 0x031E):
                # Settings/theme, non-client activation, DWM composition changes.
                self.controller.schedule_refresh()
        return False, 0


class NativeCaptionController(QObject):
    def __init__(self, app: QApplication, backend):
        super().__init__(app)
        self.app = app
        self.backend = backend
        self.dark = False
        self.colors = {}
        self.pending = set()
        self._applying_windows = set()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self.refresh)
        app.installEventFilter(self)

    def set_theme(self, dark: bool, colors: dict) -> None:
        self.dark, self.colors = dark, dict(colors)
        self.refresh()
        self.schedule_refresh()

    def schedule_refresh(self):
        # Caption painting can synchronously deliver native activation events.
        # Those are consequences of this refresh, not another theme change.
        if not self._applying_windows:
            self.refresh_timer.start(0)

    def refresh(self):
        for window in self.app.topLevelWidgets():
            self.apply_window(window)

    def apply_window(self, window):
        if not self.colors or not isValid(window) or not isinstance(window, QWidget):
            return
        if not window.isWindow() or not window.testAttribute(Qt.WidgetAttribute.WA_WState_Created):
            return
        if window.windowType() not in (Qt.WindowType.Window, Qt.WindowType.Dialog, Qt.WindowType.Tool):
            return
        if window.windowFlags() & Qt.WindowType.FramelessWindowHint:
            return
        hwnd = int(window.effectiveWinId())
        if hwnd and hwnd not in self._applying_windows:
            self._applying_windows.add(hwnd)
            try:
                self.backend.apply(hwnd, self.dark, self.colors, window.isActiveWindow())
            finally:
                self._applying_windows.discard(hwnd)

    def eventFilter(self, watched, event):
        if isinstance(watched, QWidget) and watched.isWindow() and event.type() in (
            QEvent.Type.Show, QEvent.Type.WinIdChange,
            QEvent.Type.WindowActivate, QEvent.Type.WindowDeactivate,
        ):
            if int(watched.effectiveWinId()) in self._applying_windows:
                return False
            self.apply_window(watched)
            # Qt can finish updating native styles after Show/WinIdChange.
            key = id(watched)
            if key not in self.pending:
                self.pending.add(key)
                reference = weakref.ref(watched)
                def later():
                    self.pending.discard(key)
                    window = reference()
                    if window is not None and isValid(window):
                        self.apply_window(window)
                QTimer.singleShot(0, self, later)
        return False


def sync_native_captions(app: QApplication, config: dict, colors: dict) -> None:
    """A single application-owned controller covers existing and future dialogs."""
    if sys.platform != 'win32' or app.platformName() != 'windows':
        return
    controller = getattr(app, '_native_caption_controller', None)
    if controller is None:
        try:
            backend = WindowsCaptionBackend()
        except (OSError, AttributeError):
            return
        controller = NativeCaptionController(app, backend)
        controller.native_filter = _SystemThemeEvents(controller)
        app.installNativeEventFilter(controller.native_filter)
        app._native_caption_controller = controller
    controller.set_theme(config.get('theme', 'light') == 'dark', colors)
