"""Display-only line spacing for the existing plain-text prose editor."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QPlainTextDocumentLayout, QPlainTextEdit


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
        y = 0.0
        trailing = 0.0
        for index in range(layout.lineCount()):
            line = layout.lineAt(index)
            natural = line.height() + min(0, math.ceil(line.leading()))
            line.setPosition(QPointF(line.x(), y))
            trailing = natural * (self.factor - 1.0)
            y += natural * self.factor
        rect.setHeight(layout.boundingRect().height() + trailing)
        if not block.next().isValid():
            rect.setHeight(rect.height() + self.document().documentMargin())
        return rect

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
        scroll = self.verticalScrollBar().value()
        self.spacing_layout.set_factor(factor)
        self.verticalScrollBar().setValue(scroll)
        self.viewport().update()

    def inputMethodEvent(self, event):  # noqa: N802 - Qt virtual
        super().inputMethodEvent(event)
        if not self._has_preedit():
            self.set_line_spacing(self._requested_factor)

    def setPlainText(self, text):  # noqa: N802 - Qt API
        super().setPlainText(text)
        # A document load can clear composition without a final IME event.
        # Apply its pending display preference to the newly loaded document.
        if hasattr(self, "_requested_factor"):
            self.set_line_spacing(self._requested_factor)

    def clear(self):
        super().clear()
        if hasattr(self, "_requested_factor"):
            self.set_line_spacing(self._requested_factor)
