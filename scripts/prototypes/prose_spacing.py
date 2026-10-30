"""Experimental display-only spacing. Not imported by the application.

Run the companion validation script before considering production integration.
Only public Qt layout APIs are used; no QTextCursor formatting, document
replacement, undo suppression or modified-state reset is involved.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QPlainTextDocumentLayout, QPlainTextEdit


class SpacedPlainTextLayout(QPlainTextDocumentLayout):
    """Proof of concept: move visual lines without changing block formats."""

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
        # Qt's base rectangle was measured before repositioning the lines.
        rect.setHeight(layout.boundingRect().height() + trailing)
        if not block.next().isValid():
            rect.setHeight(rect.height() + self.document().documentMargin())
        return rect

    def set_factor(self, factor: float) -> None:
        self.factor = factor
        # Existing visible layouts must also be updated when the factor returns
        # to 1.0. Unlaid-out blocks remain lazy, as in QPlainTextEdit itself.
        block = self.document().firstBlock()
        while block.isValid():
            if block.layout().lineCount():
                self.blockBoundingRect(block)
            block = block.next()
        self.documentSizeChanged.emit(self.documentSize())
        self.requestUpdate()


class SpacedProseEdit(QPlainTextEdit):
    """Isolated experimental widget; leaves IME composition uninterrupted."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.spacing_layout = SpacedPlainTextLayout(self.document())
        self.document().setDocumentLayout(self.spacing_layout)
        self._composing = False
        self._pending_factor = None

    def set_line_spacing(self, factor: float) -> None:
        factor = float(factor)
        if not math.isfinite(factor) or not 1.0 <= factor <= 2.4:
            raise ValueError("line spacing must be between 1.0 and 2.4")
        if self._composing:
            self._pending_factor = factor
            return
        if factor == self.spacing_layout.factor:
            return
        scroll = self.verticalScrollBar().value()
        self.spacing_layout.set_factor(factor)
        self.verticalScrollBar().setValue(scroll)
        self.viewport().update()

    def inputMethodEvent(self, event):  # noqa: N802 - Qt virtual
        super().inputMethodEvent(event)
        self._composing = bool(event.preeditString())
        if not self._composing and self._pending_factor is not None:
            factor, self._pending_factor = self._pending_factor, None
            self.set_line_spacing(factor)
