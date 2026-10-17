"""Plain labels that also wrap uninterrupted names without clipping."""
from math import ceil

from PySide6.QtCore import QPointF, QSize, Qt
from PySide6.QtGui import QPainter, QTextLayout, QTextOption
from PySide6.QtWidgets import QLabel, QSizePolicy


class WrappingLabel(QLabel):
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setWordWrap(True)
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def _text_layout(self, width):
        layout = QTextLayout(self.text(), self.font())
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        layout.beginLayout()
        height = 0.0
        while True:
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(max(1, width))
            line.setPosition(QPointF(0, height))
            height += line.height()
        layout.endLayout()
        return layout, max(self.fontMetrics().height(), ceil(height))

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._text_layout(width)[1]

    def minimumSizeHint(self):
        return QSize(0, self.fontMetrics().height())

    def sizeHint(self):
        width = min(240, max(1, self.fontMetrics().horizontalAdvance(self.text())))
        return QSize(width, self.heightForWidth(width))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setPen(self.palette().color(self.foregroundRole()))
        rect = self.contentsRect()
        layout, _height = self._text_layout(rect.width())
        layout.draw(painter, QPointF(rect.left(), rect.top()))
