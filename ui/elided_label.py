"""Single-line text that keeps its full value and tooltip when space is limited."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QLabel


class ElidedLabel(QLabel):
    def paintEvent(self, event):
        painter = QPainter(self)
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        self.style().drawItemText(painter, rect, int(self.alignment()), self.palette(), self.isEnabled(), text, self.foregroundRole())
