"""Window movement, resizing and Windows non-client integration for the workbench."""
from __future__ import annotations

import sys
from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QMenu

# Win32 hit-test values. Keep the geometry rule platform-independent and testable.
HTCLIENT, HTCAPTION, HTMAXBUTTON = 1, 2, 9
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17


def resize_hit_test(x, y, width, height, border, maximized=False):
    """Return a sizing hit only inside the outer border, never when maximized."""
    if maximized or not (0 <= x < width and 0 <= y < height):
        return None
    left, right = x < border, x >= width - border
    top, bottom = y < border, y >= height - border
    if top:
        return HTTOPLEFT if left else HTTOPRIGHT if right else HTTOP
    if bottom:
        return HTBOTTOMLEFT if left else HTBOTTOMRIGHT if right else HTBOTTOM
    return HTLEFT if left else HTRIGHT if right else None


RESIZE_EDGES = {
    HTLEFT: Qt.Edge.LeftEdge, HTRIGHT: Qt.Edge.RightEdge,
    HTTOP: Qt.Edge.TopEdge, HTBOTTOM: Qt.Edge.BottomEdge,
    HTTOPLEFT: Qt.Edge.TopEdge | Qt.Edge.LeftEdge,
    HTTOPRIGHT: Qt.Edge.TopEdge | Qt.Edge.RightEdge,
    HTBOTTOMLEFT: Qt.Edge.BottomEdge | Qt.Edge.LeftEdge,
    HTBOTTOMRIGHT: Qt.Edge.BottomEdge | Qt.Edge.RightEdge,
}


class WindowChromeController(QObject):
    """Keep platform window mechanics outside navigation and document logic."""

    def __init__(self, window, title_bar):
        super().__init__(window)
        self.window = window
        self.title_bar = title_bar
        self.native_frame = None
        if sys.platform == "win32" and QApplication.platformName() == "windows":
            self.native_frame = WindowsWindowFrame(window, title_bar)
        title_bar.move_requested.connect(self.start_move)
        title_bar.maximize_requested.connect(self.toggle_maximized)
        title_bar.system_menu_requested.connect(self.show_system_menu)
        self.system_menu_shortcut = QShortcut(QKeySequence("Alt+Space"), window)
        self.system_menu_shortcut.activated.connect(
            lambda: self.show_system_menu(title_bar.mapToGlobal(QPoint(12, title_bar.height())))
        )
        # Windows handles edges natively. Avoid an application-wide filter there,
        # and on headless test surfaces: style changes can generate many events.
        if self.native_frame is not None or QApplication.platformName() in {"offscreen", "minimal"}:
            window.installEventFilter(self)
        else:
            # Also see presses delivered to edge-adjacent child widgets.
            QApplication.instance().installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.window:
            if event.type() == QEvent.Type.Show and self.native_frame is not None:
                self.native_frame.install()
            elif event.type() == QEvent.Type.WindowStateChange:
                self.title_bar.maximize_button.set_restored(self.window.isMaximized())
        if (self.native_frame is None and event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
                and getattr(watched, "window", lambda: None)() is self.window):
            pos = self.window.mapFromGlobal(event.globalPosition().toPoint())
            hit = resize_hit_test(pos.x(), pos.y(), self.window.width(), self.window.height(),
                                  6, self.window.isMaximized() or self.window.isFullScreen())
            handle = self.window.windowHandle()
            if hit is not None and handle is not None and handle.startSystemResize(RESIZE_EDGES[hit]):
                return True
        return super().eventFilter(watched, event)

    def start_move(self):
        handle = self.window.windowHandle()
        if handle is not None:
            handle.startSystemMove()

    def toggle_maximized(self):
        if self.window.isMaximized():
            self.window.showNormal()
        else:
            self.window.showMaximized()

    def show_system_menu(self, pos):
        if self.native_frame is not None:
            self.native_frame.show_system_menu(pos)
            return
        menu = QMenu(self.window)
        menu.addAction("还原", self.window.showNormal).setEnabled(self.window.isMaximized())
        menu.addAction("最小化", self.window.showMinimized)
        menu.addAction("最大化", self.window.showMaximized).setEnabled(not self.window.isMaximized())
        menu.addSeparator()
        menu.addAction("关闭", self.window.close)
        menu.exec(pos)
        menu.deleteLater()

    def native_event(self, event_type, message):
        if self.native_frame is not None:
            return self.native_frame.handle_message(message)
        return None


class WindowsWindowFrame:
    """Preserve taskbar, native sizing, snapping and system-menu behavior.

    Qt paints the entire client surface. Win32 retains a resizable overlapped
    window underneath it; no translucent surface or external package is needed.
    """

    def __init__(self, window, title_bar):
        import ctypes
        from ctypes import wintypes
        self.ctypes, self.types = ctypes, wintypes
        self.window, self.title_bar = window, title_bar
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.hwnd = None
        self.max_pressed = False
        self.get_style = getattr(self.user32, "GetWindowLongPtrW", self.user32.GetWindowLongW)
        self.set_style = getattr(self.user32, "SetWindowLongPtrW", self.user32.SetWindowLongW)
        self.get_style.argtypes = [wintypes.HWND, ctypes.c_int]
        self.get_style.restype = ctypes.c_ssize_t
        self.set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
        self.set_style.restype = ctypes.c_ssize_t
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.IsZoomed.argtypes = [wintypes.HWND]
        self.user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        self.user32.MonitorFromWindow.restype = wintypes.HANDLE
        self.user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self.user32.GetSystemMenu.argtypes = [wintypes.HWND, wintypes.BOOL]
        self.user32.GetSystemMenu.restype = wintypes.HMENU
        self.user32.EnableMenuItem.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.UINT]
        self.user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int,
                                               ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        self.user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        self.user32.SetCapture.argtypes = [wintypes.HWND]
        self.user32.SetCapture.restype = wintypes.HWND

        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

        class MinMaxInfo(ctypes.Structure):
            _fields_ = [(name, wintypes.POINT) for name in
                        ("reserved", "maxSize", "maxPosition", "minTrackSize", "maxTrackSize")]

        class NcCalcSize(ctypes.Structure):
            _fields_ = [("rects", wintypes.RECT * 3), ("position", ctypes.c_void_p)]

        class TrackMouseEvent(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                        ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD)]

        self.MonitorInfo, self.MinMaxInfo = MonitorInfo, MinMaxInfo
        self.NcCalcSize, self.TrackMouseEvent = NcCalcSize, TrackMouseEvent
        self.user32.TrackMouseEvent.argtypes = [ctypes.POINTER(TrackMouseEvent)]

    def install(self):
        hwnd = int(self.window.winId())
        if self.hwnd == hwnd:
            return
        self.hwnd = hwnd
        # WS_CAPTION | WS_THICKFRAME | WS_SYSMENU | WS_MINIMIZEBOX | WS_MAXIMIZEBOX.
        # WM_NCCALCSIZE removes only the visible system frame, not these capabilities.
        self.set_style(hwnd, -16, self.get_style(hwnd, -16) | 0x00CF0000)
        self.user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0027)  # FRAMECHANGED/NOMOVE/NOSIZE/NOZORDER
        try:
            dwm = self.ctypes.WinDLL("dwmapi")
            dwm.DwmSetWindowAttribute.argtypes = [
                self.types.HWND, self.types.DWORD, self.ctypes.c_void_p, self.types.DWORD]
            policy = self.ctypes.c_int(2)  # Enable DWM non-client rendering and rounded corners.
            dwm.DwmSetWindowAttribute(hwnd, 2, self.ctypes.byref(policy), 4)
            dwm.DwmSetWindowAttribute(hwnd, 33, self.ctypes.byref(policy), 4)
        except (AttributeError, OSError):
            pass

    def monitor_info(self, hwnd):
        info = self.MonitorInfo()
        info.cbSize = self.ctypes.sizeof(info)
        monitor = self.user32.MonitorFromWindow(hwnd, 2)  # nearest monitor, including negative origins
        return info if self.user32.GetMonitorInfoW(monitor, self.ctypes.byref(info)) else None

    def qt_position(self, hwnd, x, y):
        point = self.types.POINT(x, y)
        self.user32.ScreenToClient(hwnd, self.ctypes.byref(point))
        ratio = self.window.devicePixelRatioF()
        return QPoint(round(point.x / ratio), round(point.y / ratio))

    def max_contains(self, pos):
        button = self.title_bar.maximize_button
        return button.isVisible() and button.rect().contains(button.mapFrom(self.window, pos))

    def set_max_hover(self, hover):
        button = self.title_bar.maximize_button
        if button.property("nativeHover") != hover:
            button.setProperty("nativeHover", hover)
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def show_system_menu(self, pos):
        hwnd = int(self.window.winId())
        point = self.types.POINT(
            round(self.window.mapFromGlobal(pos).x() * self.window.devicePixelRatioF()),
            round(self.window.mapFromGlobal(pos).y() * self.window.devicePixelRatioF()))
        self.user32.ClientToScreen(hwnd, self.ctypes.byref(point))
        menu = self.user32.GetSystemMenu(hwnd, False)
        maximized = self.window.isMaximized()
        for command, enabled in ((0xF120, maximized), (0xF030, not maximized),
                                 (0xF000, not maximized), (0xF010, not maximized)):
            self.user32.EnableMenuItem(menu, command, 0 if enabled else 1)
        command = self.user32.TrackPopupMenu(menu, 0x0102, point.x, point.y, 0, hwnd, None)
        if command:
            # WM_SYSCOMMAND; SC_CLOSE still enters QMainWindow.closeEvent.
            self.user32.PostMessageW(hwnd, 0x0112, command, 0)

    def handle_message(self, message):
        c, w = self.ctypes, self.types
        msg = w.MSG.from_address(int(message))
        hwnd = msg.hWnd
        if msg.message in (0x00A1, 0x00A3, 0x00A4):  # Native non-client pointer presses.
            # HTMAXBUTTON bypasses Qt mouse events; use the same focus treatment.
            self.title_bar.dismiss_window_control_focus()
        if msg.message == 0x0083:  # WM_NCCALCSIZE
            if msg.wParam and self.user32.IsZoomed(hwnd):
                info = self.monitor_info(hwnd)
                if info is not None:
                    params = self.NcCalcSize.from_address(msg.lParam)
                    params.rects[0] = info.rcWork
            return True, 0
        if msg.message == 0x0024:  # WM_GETMINMAXINFO
            info = self.monitor_info(hwnd)
            if info is not None:
                limits = self.MinMaxInfo.from_address(msg.lParam)
                work, monitor = info.rcWork, info.rcMonitor
                limits.maxPosition.x = work.left - monitor.left
                limits.maxPosition.y = work.top - monitor.top
                limits.maxSize.x, limits.maxSize.y = work.right - work.left, work.bottom - work.top
                ratio = self.window.devicePixelRatioF()
                limits.minTrackSize.x = min(limits.maxSize.x, round(self.window.minimumWidth() * ratio))
                limits.minTrackSize.y = min(limits.maxSize.y, round(self.window.minimumHeight() * ratio))
                return True, 0
        if msg.message == 0x0084:  # WM_NCHITTEST; coordinates are signed physical screen pixels.
            x, y = c.c_short(msg.lParam & 0xffff).value, c.c_short((msg.lParam >> 16) & 0xffff).value
            rect = w.RECT()
            if not self.user32.GetWindowRect(hwnd, c.byref(rect)):
                return None
            ratio = self.window.devicePixelRatioF()
            hit = resize_hit_test(x - rect.left, y - rect.top, rect.right - rect.left,
                                  rect.bottom - rect.top, round(6 * ratio),
                                  self.window.isMaximized() or self.window.isFullScreen())
            if hit is not None:
                return True, hit
            pos = self.qt_position(hwnd, x, y)
            if self.max_contains(pos):
                return True, HTMAXBUTTON  # Windows 11 Snap Layouts.
            local = self.title_bar.mapFrom(self.window, pos)
            if self.title_bar.is_drag_position(local):
                return True, HTCAPTION
            return True, HTCLIENT
        if msg.message == 0x00A4 and msg.wParam == HTCAPTION:  # WM_NCRBUTTONDOWN
            return True, 0
        if msg.message == 0x00A5 and msg.wParam == HTCAPTION:  # WM_NCRBUTTONUP
            x, y = c.c_short(msg.lParam & 0xffff).value, c.c_short((msg.lParam >> 16) & 0xffff).value
            self.show_system_menu(self.window.mapToGlobal(self.qt_position(hwnd, x, y)))
            return True, 0
        if msg.message == 0x00A0:  # WM_NCMOUSEMOVE
            self.set_max_hover(msg.wParam == HTMAXBUTTON)
            tracking = self.TrackMouseEvent(c.sizeof(self.TrackMouseEvent), 0x12, hwnd, 0)
            self.user32.TrackMouseEvent(c.byref(tracking))  # LEAVE | NONCLIENT
            # Allow DefWindowProc to present the system snap flyout.
        if msg.message == 0x02A2:  # WM_NCMOUSELEAVE
            self.set_max_hover(False)
        if msg.message == 0x00A1 and msg.wParam == HTMAXBUTTON:  # WM_NCLBUTTONDOWN
            self.max_pressed = True
            self.title_bar.maximize_button.setDown(True)
            self.user32.SetCapture(hwnd)
            return True, 0
        if self.max_pressed and msg.message in (0x0200, 0x0202, 0x00A2):
            point = w.POINT()
            self.user32.GetCursorPos(c.byref(point))
            inside = self.max_contains(self.qt_position(hwnd, point.x, point.y))
            self.title_bar.maximize_button.setDown(inside)
            if msg.message != 0x0200:
                self.max_pressed = False
                self.user32.ReleaseCapture()
                self.title_bar.maximize_button.setDown(False)
                if inside:
                    self.title_bar.maximize_button.click()
            return True, 0
        if msg.message == 0x0215:  # WM_CAPTURECHANGED
            self.max_pressed = False
            self.title_bar.maximize_button.setDown(False)
        return None
