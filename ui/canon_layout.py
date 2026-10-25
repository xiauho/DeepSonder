"""Content-sized text editors and adaptive headers for canon cards."""
from math import ceil

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget)


class CanonCard(QFrame):
    """A flat surface card; grouping is carried by dividers, not nested shadows."""

    def __init__(self, name="canonModuleFrame", parent=None):
        super().__init__(parent)
        self.setObjectName(name)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)


class CanonFieldsGrid(QWidget):
    """Reposition existing field widgets; never recreate their inputs or tab chain."""
    reflowed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("canonFieldsGrid")
        self.setMinimumWidth(0)
        self._fields = []
        self._signature = None
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(16)
        self._grid.setVerticalSpacing(10)
        self._grid.setColumnStretch(0, 1)
        self._grid.setColumnStretch(1, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._reflow)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)

    def add_field(self, field, label, editor, *, short=False):
        self._fields.append((field, label, editor, short))
        self._grid.addWidget(field, len(self._fields) - 1, 0, 1, 2)
        editor.textChanged.connect(self._schedule)
        self._schedule()

    def _schedule(self, *_):
        self._timer.start(0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._schedule()

    def _reflow(self):
        minimum_column = self.fontMetrics().horizontalAdvance("模" * 16) + 32
        double = self.width() >= 2 * minimum_column + self._grid.horizontalSpacing()
        column_width = (self.width() - self._grid.horizontalSpacing()) // 2
        positions = []
        pending = None
        row = 0
        for index, (_field, label, editor, short) in enumerate(self._fields):
            fits = (double and short
                    and label.fontMetrics().horizontalAdvance(label.text()) <= column_width
                    and editor.fontMetrics().horizontalAdvance(editor.text()) <= column_width - 32)
            if fits and pending is None:
                pending = index
            elif fits:
                positions.extend(((pending, row, 0, 1), (index, row, 1, 1)))
                pending = None
                row += 1
            else:
                if pending is not None:
                    positions.append((pending, row, 0, 2))
                    row += 1
                    pending = None
                positions.append((index, row, 0, 2))
                row += 1
        if pending is not None:
            positions.append((pending, row, 0, 2))
        signature = (self.width(), self.fontMetrics().height(), tuple(positions))
        if signature == self._signature:
            return
        self._signature = signature
        # Removing layout items keeps widget parents, focus and native cursors.
        while self._grid.count():
            self._grid.takeAt(0)
        for old_row in range(self._grid.rowCount()):
            self._grid.setRowMinimumHeight(old_row, 0)
            self._grid.setRowStretch(old_row, 0)
        for index, row, column, span in positions:
            self._grid.addWidget(self._fields[index][0], row, column, 1, span)
        self.reflowed.emit()


class CanonIntroCard(CanonCard):
    """Keep an empty introduction compact while retaining its live text controls."""
    visibility_changed = Signal()

    def __init__(self, parent=None):
        super().__init__("canonIntroCard", parent)
        self._editors = []
        self._expanded = False
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 10, 16, 12)
        self._layout.setSpacing(10)
        header = QHBoxLayout()
        self.title = QLabel("资料导语")
        self.title.setObjectName("canonModuleTitle")
        header.addWidget(self.title)
        self.toggle = QPushButton("＋ 添加资料概述")
        self.toggle.setCheckable(True)
        self.toggle.setObjectName("canonIntroToggle")
        self.toggle.setAccessibleName("添加资料概述")
        self.toggle.clicked.connect(self._toggle)
        header.addWidget(self.toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        header.addStretch()
        self._layout.addLayout(header)

    def add_editor(self, editor):
        self._editors.append(editor)
        self._layout.addWidget(editor)
        # Document signals also run when history restores block editor signals.
        editor.document().contentsChanged.connect(self._sync)
        self._sync()

    def _toggle(self):
        self._expanded = not self._expanded
        if not self._expanded:
            self.toggle.setFocus()
        self._sync()
        if self._expanded and self._editors:
            self._editors[0].setFocus()
        else:
            self.toggle.setFocus()

    def expand(self):
        self._expanded = True
        self._sync()

    def _sync(self):
        content = any(editor.toPlainText().strip() for editor in self._editors)
        if content or QApplication.focusWidget() in self._editors:
            self._expanded = True
        expanded = content or self._expanded
        changed = any(editor.isHidden() == expanded for editor in self._editors)
        self.title.setVisible(expanded)
        self.toggle.setVisible(not content)
        self.toggle.setChecked(expanded)
        self.toggle.setText("收起概述" if expanded else "＋ 添加资料概述")
        self.toggle.setAccessibleName("收起空白资料概述" if expanded else "添加资料概述")
        self.toggle.setToolTip("展开概述输入，不修改资料内容" if not expanded else "收起空白概述，不影响保存")
        for editor in self._editors:
            editor.setVisible(expanded)
        if changed:
            self.visibility_changed.emit()


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
