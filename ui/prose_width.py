"""Reading-width preferences and a pointer/keyboard resize affordance."""

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QPainter, QPalette, QPen
from PySide6.QtWidgets import QApplication, QToolButton


MIN_PROSE_WIDTH = 480
MAX_PROSE_WIDTH = 1600
DEFAULT_PROSE_WIDTH = 760
PROSE_WIDTH_MODES = {"recommended", "custom", "fill"}


def reading_width_preferences(value, legacy=True):
    """Validate profile data; old booleans remain recommended/fill preferences."""
    fallback = "fill" if legacy is False else "recommended"
    if not isinstance(value, dict) or not isinstance(value.get("mode"), str) or value["mode"] not in PROSE_WIDTH_MODES:
        return {"mode": fallback, "custom_width": DEFAULT_PROSE_WIDTH}
    width = value.get("custom_width", DEFAULT_PROSE_WIDTH)
    if type(width) is not int:
        width = DEFAULT_PROSE_WIDTH
    return {"mode": value["mode"], "custom_width": max(MIN_PROSE_WIDTH, min(MAX_PROSE_WIDTH, width))}


class ProseWidthHandle(QToolButton):
    """Dragging preserves input focus; Tab/Enter and arrow keys offer alternatives."""

    drag_started = Signal()
    dragged = Signal(int)
    drag_finished = Signal()
    drag_cancelled = Signal()
    stepped = Signal(int)
    reset_requested = Signal()

    def __init__(self, side, parent=None):
        super().__init__(parent)
        self.side = side
        self.setObjectName("proseWidthHandle")
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName(f"调整正文{'左' if side == 'left' else '右'}侧行宽")
        self.setToolTip("拖动调整正文行宽；单击或 Enter 输入数值。\n方向键微调，Shift 加速，Home 恢复推荐；Esc 取消拖动。")
        self._press = None
        self._dragging = False

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        pen = QPen(self.palette().color(QPalette.ColorRole.ButtonText), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        center = self.rect().center()
        painter.drawLine(center.x(), center.y() - 18, center.x(), center.y() + 18)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = event.globalPosition().toPoint()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return super().mouseMoveEvent(event)
        delta = event.globalPosition().toPoint() - self._press
        if not self._dragging and abs(delta.x()) >= QApplication.startDragDistance():
            self._dragging = True
            QApplication.instance().installEventFilter(self)
            self.drag_started.emit()
        if self._dragging:
            self.dragged.emit(delta.x())
        event.accept()

    def _end_drag(self, cancelled=False):
        active = self._dragging
        self._press = None
        self._dragging = False
        QApplication.instance().removeEventFilter(self)
        if active:
            (self.drag_cancelled if cancelled else self.drag_finished).emit()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._press is not None:
            dragged = self._dragging
            self._end_drag()
            if not dragged and self.rect().contains(event.position().toPoint()):
                self.clicked.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def hideEvent(self, event):
        self._end_drag(cancelled=True)
        super().hideEvent(event)

    def event(self, event):
        if event.type() == QEvent.Type.UngrabMouse and self._dragging:
            self._end_drag(cancelled=True)
        return super().event(event)

    def eventFilter(self, watched, event):
        if self._dragging and watched is self.window() and event.type() == QEvent.Type.WindowDeactivate:
            self._end_drag(cancelled=True)
        if self._dragging and event.type() in (QEvent.Type.ShortcutOverride, QEvent.Type.KeyPress):
            if event.key() == Qt.Key.Key_Escape:
                event.accept()
                if event.type() == QEvent.Type.KeyPress:
                    self._end_drag(cancelled=True)
                return True
        return super().eventFilter(watched, event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            step = 64 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 16
            direction = -1 if event.key() == Qt.Key.Key_Left else 1
            self.stepped.emit(direction * step * (-1 if self.side == "left" else 1))
            event.accept()
        elif event.key() == Qt.Key.Key_Home:
            self.reset_requested.emit()
            event.accept()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.clicked.emit()
            event.accept()
        else:
            super().keyPressEvent(event)
