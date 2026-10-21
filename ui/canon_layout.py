"""Content-sized text editors and adaptive headers for canon cards."""
from math import ceil

from PySide6.QtCore import QEvent, QTimer, Qt
from PySide6.QtWidgets import (QFrame, QGridLayout,
    QPlainTextEdit, QSizePolicy, QWidget)


class CanonCard(QFrame):
    """A flat surface card; grouping is carried by dividers, not nested shadows."""

    def __init__(self, name="canonModuleFrame", parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)


class CanonTextEdit(QPlainTextEdit):
    """Grow with wrapped text up to a readable number of lines, then scroll."""

    def __init__(self, text="", parent=None, *, minimum_lines=2, maximum_lines=10):
        super().__init__(parent)
        self.setObjectName("canonTextEditor")
        self._minimum_lines = minimum_lines
        self._maximum_lines = maximum_lines
        self._height_timer = QTimer(self)
        self._height_timer.setSingleShot(True)
        self._height_timer.timeout.connect(self._fit_content)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(self._schedule_fit)
        self.textChanged.connect(self._schedule_fit)
        self.setPlainText(text)
        self._schedule_fit()

    def _schedule_fit(self, *_args):
        self._height_timer.start(0)

    def _fit_content(self):
        # QPlainTextDocumentLayout's document height is a block count, not pixels.
        # Query block rectangles so wrapped lines outside the viewport count too.
        line_height = self.fontMetrics().lineSpacing()
        height = 0.0
        block = self.document().begin()
        limit = self._maximum_lines * line_height
        layout = self.document().documentLayout()
        while block.isValid() and height < limit:
            height += layout.blockBoundingRect(block).height()
            block = block.next()
        # Styled frame margins already include its border and padding.
        margins = self.contentsMargins()
        chrome = 2 * self.document().documentMargin() + margins.top() + margins.bottom()
        target = ceil(max(self._minimum_lines * line_height, min(limit, height)) + chrome)
        if self.height() != target or self.minimumHeight() != target:
            self.setFixedHeight(target)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._schedule_fit()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._schedule_fit()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_fit()


class CanonModuleHeader(QWidget):
    """Keep actions beside the title, moving them below when space is tight."""

    def __init__(self, heading, operations=None, parent=None):
        super().__init__(parent)
        self.setObjectName("canonModuleHeader")
        self.heading = heading
        self.operations = operations
        self._stacked = None
        self._reflow_timer = QTimer(self)
        self._reflow_timer.setSingleShot(True)
        self._reflow_timer.timeout.connect(self._reflow)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(8)
        self._grid.addWidget(heading, 0, 0)
        self._grid.setColumnStretch(0, 1)
        if operations is not None:
            self._grid.addWidget(operations, 0, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reflow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._reflow_timer.start(0)

    def showEvent(self, event):
        super().showEvent(event)
        self._reflow_timer.start(0)

    def event(self, event):
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest and hasattr(self, "_reflow_timer"):
            self._reflow_timer.start(0)
        return handled

    def _reflow(self):
        if self.operations is None:
            return
        # Reserve a useful title measure even for very long headings.
        title_width = self.fontMetrics().horizontalAdvance("模" * 10) + 40
        stacked = self.width() < title_width + self.operations.sizeHint().width() + 12
        if stacked != self._stacked:
            self._stacked = stacked
            self._grid.removeWidget(self.operations)
            self._grid.addWidget(self.operations, 1 if stacked else 0, 0 if stacked else 1,
                alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
