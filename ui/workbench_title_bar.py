"""Persistent, theme-aware window controls above every workbench route."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenuBar, QSizePolicy, QToolButton


class WindowControlButton(QToolButton):
    """Draw caption glyphs without depending on an installed icon font."""

    pointer_pressed = Signal()

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.restored = False
        self._keyboard_focus = False
        self.setObjectName("windowCloseButton" if kind == "close" else "windowControlButton")
        self.setFixedSize(46, 44)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setMouseTracking(True)
        self.set_label({"minimize": "最小化", "maximize": "最大化", "close": "关闭"}[kind])

    def set_label(self, label: str) -> None:
        self.setAccessibleName(label)
        self.setToolTip(label)

    def set_restored(self, restored: bool) -> None:
        self.restored = restored
        self.set_label("还原" if restored else "最大化")
        self.update()

    def clear_keyboard_focus_indicator(self) -> None:
        self._keyboard_focus = False
        self.update()

    def focusInEvent(self, event):
        reason = event.reason()
        if reason in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                      Qt.FocusReason.ShortcutFocusReason):
            self._keyboard_focus = True
        elif reason not in (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason):
            self._keyboard_focus = False
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event):
        # Preserve the input origin across window deactivation and popup menus.
        if event.reason() not in (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason):
            self._keyboard_focus = False
        super().focusOutEvent(event)
        self.update()

    def mousePressEvent(self, event):
        self.clear_keyboard_focus_indicator()
        self.pointer_pressed.emit()
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        self._keyboard_focus = True
        self.update()
        super().keyPressEvent(event)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange:
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self.palette().buttonText().color()
        if self.kind == "close" and (self.underMouse() or self.isDown()):
            color = Qt.GlobalColor.white
        painter.setPen(QPen(color, 1.2))
        x, y = self.width() // 2, self.height() // 2
        if self.kind == "minimize":
            painter.drawLine(x - 5, y, x + 5, y)
        elif self.kind == "close":
            painter.drawLine(x - 5, y - 5, x + 5, y + 5)
            painter.drawLine(x + 5, y - 5, x - 5, y + 5)
        elif self.restored:
            # Two offset outlines, with no dependency on a font or theme background.
            painter.drawLine(x - 3, y - 5, x + 5, y - 5)
            painter.drawLine(x + 5, y - 5, x + 5, y + 3)
            painter.drawLine(x - 3, y - 5, x - 3, y - 3)
            painter.drawLine(x + 3, y + 3, x + 5, y + 3)
            painter.drawRect(x - 5, y - 3, 8, 8)
        else:
            painter.drawRect(x - 5, y - 5, 10, 10)
        if self.hasFocus() and self._keyboard_focus and self.isActiveWindow():
            painter.setPen(QPen(self.palette().highlight().color(), 2))
            painter.drawRect(self.rect().adjusted(1, 1, -1, -1))


class WorkbenchTitleBar(QFrame):
    move_requested = Signal()
    maximize_requested = Signal()
    system_menu_requested = Signal(QPoint)

    def __init__(self, window):
        super().__init__(window)
        self.setObjectName("workbenchTitleBar")
        self.setFixedHeight(44)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(0)
        self.brand = QLabel("DeepSonder", self)
        self.brand.setObjectName("windowBrand")
        self.brand.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.brand)
        layout.addSpacing(12)
        self.menu_bar = QMenuBar(self)
        self.menu_bar.setNativeMenuBar(False)
        # QMenuBar places its action row at the top of its own rectangle.
        # Center its natural height instead of stretching that rectangle to 44px.
        self.menu_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.menu_bar, 0, Qt.AlignmentFlag.AlignVCenter)
        self.title = QLabel(self)
        self.title.setObjectName("windowProjectTitle")
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.title, 1)
        self.exit_focus_button = QToolButton(self)
        self.exit_focus_button.setObjectName("windowExitFocusButton")
        self.exit_focus_button.setText("退出专注")
        self.exit_focus_button.setToolTip("退出专注模式（Esc）")
        self.exit_focus_button.setAccessibleName("退出专注模式")
        self.exit_focus_button.setMinimumHeight(36)
        self.exit_focus_button.hide()
        layout.addWidget(self.exit_focus_button)
        self.minimize_button = WindowControlButton("minimize", self)
        self.maximize_button = WindowControlButton("maximize", self)
        self.close_button = WindowControlButton("close", self)
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            layout.addWidget(button)
            button.pointer_pressed.connect(self.dismiss_window_control_focus)
        self.minimize_button.clicked.connect(window.showMinimized)
        self.maximize_button.clicked.connect(self.maximize_requested)
        self.close_button.clicked.connect(window.close)
        window.windowTitleChanged.connect(self._set_title)
        self._set_title(window.windowTitle())
        self.title.installEventFilter(self)

    def dismiss_window_control_focus(self) -> None:
        for button in (self.minimize_button, self.maximize_button, self.close_button):
            button.clear_keyboard_focus_indicator()

    def _set_title(self, text: str) -> None:
        self._full_title = text.removeprefix("DeepSonder-PySide6 — ").removesuffix(" - DeepSonder-PySide6").removesuffix(" — DeepSonder-PySide6")
        if self._full_title == "DeepSonder-PySide6":
            self._full_title = ""
        self.title.setToolTip(text)
        self._elide_title()

    def _elide_title(self) -> None:
        self.title.setText(self.title.fontMetrics().elidedText(
            self._full_title, Qt.TextElideMode.ElideRight, max(0, self.title.width() - 20)
        ))

    def eventFilter(self, watched, event):
        if watched is self.title and event.type() in (QEvent.Type.Resize, QEvent.Type.FontChange):
            self._elide_title()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide_title()

    def set_focus_mode(self, enabled: bool) -> None:
        self.exit_focus_button.setVisible(enabled)

    def is_drag_position(self, pos: QPoint) -> bool:
        if not self.rect().contains(pos):
            return False
        child = self.childAt(pos)
        return child is None or child in (self.brand, self.title)

    def mousePressEvent(self, event):
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self.dismiss_window_control_focus()
        if event.button() == Qt.MouseButton.LeftButton and self.is_drag_position(event.position().toPoint()):
            self.move_requested.emit()
            event.accept()
        elif event.button() == Qt.MouseButton.RightButton and self.is_drag_position(event.position().toPoint()):
            self.system_menu_requested.emit(event.globalPosition().toPoint())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_drag_position(event.position().toPoint()):
            self.maximize_requested.emit()
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)
