"""Display-only line spacing for the existing plain-text prose editor."""

from __future__ import annotations

import math

from PySide6.QtCore import QEvent, QPoint, QPointF
from PySide6.QtGui import QTextBlockUserData, QTextCursor
from PySide6.QtWidgets import QPlainTextDocumentLayout, QPlainTextEdit


class _SpacingState(QTextBlockUserData):
    """Ephemeral geometry only; Qt owns its lifetime with the text block."""

    signature = None
    last_y = None


class SpacedPlainTextLayout(QPlainTextDocumentLayout):
    """Move visual lines without changing text formats or document history."""

    def __init__(self, document):
        super().__init__(document)
        self.factor = 1.0

    def blockBoundingRect(self, block):  # noqa: N802 - Qt virtual
        rect = super().blockBoundingRect(block)
        if not block.isValid() or not block.isVisible():
            return rect
        layout = block.layout()
        count = layout.lineCount()
        trailing = 0.0
        if count:
            last = layout.lineAt(count - 1)
            natural = last.height() + min(0, math.ceil(last.leading()))
            trailing = natural * (self.factor - 1.0)
            state = block.userData()
            if state is None:
                state = _SpacingState()
                block.setUserData(state)
            signature = (block.revision(), count, self.factor)
            # Qt may lay out again with unchanged text (font, width, preedit).
            # Native layout restores natural y positions, invalidating last_y.
            # Never replace user data owned by another document feature.
            cached = (isinstance(state, _SpacingState)
                      and state.signature == signature and state.last_y == last.y())
            if not cached:
                self._space_lines(layout)
                if isinstance(state, _SpacingState):
                    state.signature = signature
                    state.last_y = last.y()
        rect.setHeight(layout.boundingRect().height() + trailing)
        if not block.next().isValid():
            rect.setHeight(rect.height() + self.document().documentMargin())
        return rect

    def _space_lines(self, layout):
        y = 0.0
        for index in range(layout.lineCount()):
            line = layout.lineAt(index)
            natural = line.height() + min(0, math.ceil(line.leading()))
            line.setPosition(QPointF(line.x(), y))
            y += natural * self.factor

    def set_factor(self, factor: float) -> None:
        self.factor = factor
        # Keep unlaid-out blocks lazy; restore existing line positions even
        # when the new factor returns to 1.0. No document-format mutations.
        block = self.document().firstBlock()
        while block.isValid():
            if block.layout().lineCount():
                self.blockBoundingRect(block)
            block = block.next()
        self.documentSizeChanged.emit(self.documentSize())
        self.requestUpdate()


class ProseTextEdit(QPlainTextEdit):
    """A plain editor whose spacing changes never cancel an IME preedit."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.spacing_layout = SpacedPlainTextLayout(self.document())
        self.document().setDocumentLayout(self.spacing_layout)
        self._requested_factor = 1.0
        self._restoring_view = False
        self._loading_view = False
        self._reflowing = False
        self._capturing_anchor = False
        self._reading_cursor = QTextCursor(self.document())
        self.verticalScrollBar().valueChanged.connect(self._track_reading_anchor)

    def _track_reading_anchor(self, *_args) -> None:
        if self._restoring_view or self._loading_view or self._reflowing or self._capturing_anchor:
            return
        self._capturing_anchor = True
        try:
            self._reading_cursor.setPosition(self.cursorForPosition(QPoint(0, 0)).position())
        finally:
            self._capturing_anchor = False

    def reading_anchor(self) -> int:
        return self._reading_cursor.position()

    def restore_reading_anchor(self, position: int) -> None:
        if self._restoring_view:
            return
        self._restoring_view = True
        try:
            document = self.document()
            position = max(0, min(document.characterCount() - 1, position))
            self._reading_cursor.setPosition(position)
            block = document.findBlock(position)
            self.blockBoundingRect(block)
            line = block.layout().lineForTextPosition(position - block.position())
            visual_line = line.lineNumber() if line.isValid() else 0
            self.verticalScrollBar().setValue(block.firstLineNumber() + visual_line)
        finally:
            self._restoring_view = False

    def reading_position(self) -> dict:
        cursor = self.textCursor()
        return {"cursor": cursor.position(), "anchor": cursor.anchor(), "top_position": self.reading_anchor()}

    def restore_reading_position(self, state: dict, *, selection=True) -> None:
        maximum = self.document().characterCount() - 1
        def bounded(key):
            value = state.get(key, 0)
            return max(0, min(maximum, value)) if type(value) is int else 0
        if selection:
            cursor = self.textCursor()
            cursor.setPosition(bounded("anchor"))
            cursor.setPosition(bounded("cursor"), QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)
        self.restore_reading_anchor(bounded("top_position"))

    def resizeEvent(self, event):  # noqa: N802 - Qt virtual
        anchor = self.reading_anchor() if hasattr(self, "_restoring_view") and not self._loading_view else None
        previous = getattr(self, "_reflowing", False)
        self._reflowing = True
        try:
            super().resizeEvent(event)
        finally:
            self._reflowing = previous
        if anchor is not None:
            self.restore_reading_anchor(anchor)

    def changeEvent(self, event):  # noqa: N802 - Qt virtual
        anchor = (self.reading_anchor() if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange)
                  and hasattr(self, "_restoring_view") and not self._loading_view else None)
        previous = getattr(self, "_reflowing", False)
        self._reflowing = True
        try:
            super().changeEvent(event)
        finally:
            self._reflowing = previous
        if anchor is not None:
            self.restore_reading_anchor(anchor)

    def line_spacing(self) -> float:
        return self._requested_factor

    def _has_preedit(self) -> bool:
        return bool(self.textCursor().block().layout().preeditAreaText())

    def set_line_spacing(self, factor: float) -> None:
        factor = float(factor)
        if not math.isfinite(factor) or not 1.0 <= factor <= 2.4:
            raise ValueError("line spacing must be between 1.0 and 2.4")
        self._requested_factor = factor
        if self._has_preedit() or factor == self.spacing_layout.factor:
            return
        anchor = self.reading_anchor()
        self.spacing_layout.set_factor(factor)
        self.restore_reading_anchor(anchor)
        self.viewport().update()

    def inputMethodEvent(self, event):  # noqa: N802 - Qt virtual
        super().inputMethodEvent(event)
        if not self._has_preedit():
            self.set_line_spacing(self._requested_factor)

    def setPlainText(self, text):  # noqa: N802 - Qt API
        self._loading_view = True
        try:
            super().setPlainText(text)
        finally:
            self._loading_view = False
        if hasattr(self, "_reading_cursor"):
            self._reading_cursor.setPosition(0)
        # A document load can clear composition without a final IME event.
        # Apply its pending display preference to the newly loaded document.
        if hasattr(self, "_requested_factor"):
            self.set_line_spacing(self._requested_factor)

    def clear(self):
        self._loading_view = True
        try:
            super().clear()
        finally:
            self._loading_view = False
        if hasattr(self, "_reading_cursor"):
            self._reading_cursor.setPosition(0)
        if hasattr(self, "_requested_factor"):
            self.set_line_spacing(self._requested_factor)
