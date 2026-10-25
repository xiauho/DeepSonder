"""Find visible canon content without writing or entering the document history."""
from dataclasses import dataclass
import re

from PySide6.QtCore import QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtGui import QColor, QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QTextEdit, QToolButton, QVBoxLayout, QWidget)
from shiboken6 import isValid

from ui.canon_layout import CanonModuleHeader
from ui.icons import set_button_icon
from ui.theme import colors_for


def widget_text(widget):
    return widget.toPlainText() if isinstance(widget, QPlainTextEdit) else widget.text()


def utf16_position(text, offset):
    return len(text[:offset].encode("utf-16-le")) // 2


@dataclass(frozen=True)
class CanonFindHit:
    identity: tuple
    start: int
    end: int


class CanonFindBar(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("findBar")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 8, 18, 8)
        self.input = QLineEdit()
        self.input.setMinimumWidth(0)
        self.input.setPlaceholderText("在本份资料中查找")
        self.input.setAccessibleName("资料页内查找")
        self.input.setToolTip("查找当前草稿中的模块、字段与正文（Ctrl+F）")
        self.input.setClearButtonEnabled(True)
        actions = QWidget()
        row = QHBoxLayout(actions)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.count = QLabel("输入查找内容")
        self.count.setObjectName("mutedLabel")
        self.count.setAccessibleName("资料查找结果")
        row.addWidget(self.count)
        self.previous = QPushButton("上一项")
        self.previous.setToolTip("上一处命中（Shift+Enter／Shift+F3）")
        self.next = QPushButton("下一项")
        self.next.setToolTip("下一处命中（Enter／F3）")
        self.close = QToolButton()
        self.close.setAccessibleName("关闭资料查找")
        self.close.setToolTip("关闭查找（Esc）")
        set_button_icon(self.close, "close", size=17)
        for button in (self.previous, self.next, self.close):
            row.addWidget(button)
        layout.addWidget(CanonModuleHeader(self.input, actions))
        self.hide()


class CanonFindController(QObject):
    MAX_HITS = 5000

    def __init__(self, page, form, bar):
        super().__init__(page)
        self.form, self.bar = form, bar
        self.hits = []
        self.index = -1
        self._active = False
        self._new_query = False
        self._composing = False
        self._marked = None
        self._old_cursor = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self.refresh)
        self._reveal_timer = QTimer(self)
        self._reveal_timer.setSingleShot(True)
        self._reveal_timer.timeout.connect(self._reveal_current)
        bar.input.textChanged.connect(self._query_changed)
        bar.input.installEventFilter(self)
        bar.next.clicked.connect(self.next)
        bar.previous.clicked.connect(self.previous)
        bar.close.clicked.connect(self.close)
        form.form_about_to_rebuild.connect(self._before_rebuild)
        form.form_rebuilt.connect(self._content_changed)
        form.state_changed.connect(self._content_changed)
        form.document_replaced.connect(self.reset)
        self._shortcuts = []
        for keys, callback in (("F3", self.next), ("Shift+F3", self.previous), ("Esc", self.close)):
            shortcut = QShortcut(QKeySequence(keys), page)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(callback)
            shortcut.setEnabled(False)
            self._shortcuts.append(shortcut)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.InputMethod:
            self._composing = bool(event.preeditString())
        if event.type() in (QEvent.Type.KeyPress, QEvent.Type.ShortcutOverride):
            if self._composing:
                if event.type() == QEvent.Type.ShortcutOverride:
                    event.accept()
                    return True
                return False
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Escape):
                if event.type() == QEvent.Type.KeyPress:
                    if event.key() == Qt.Key.Key_Escape:
                        self.close()
                    elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                        self.previous()
                    else:
                        self.next()
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def show(self):
        if self.form.current_path() is None:
            return
        selected = ""
        editor = self.form._last_editor
        if editor is not None and isValid(editor):
            selected = (editor.selectedText() if isinstance(editor, QLineEdit)
                        else editor.textCursor().selectedText())
        self._active = True
        self.bar.show()
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)
        if selected and "\u2029" not in selected and len(selected) < 100:
            self.bar.input.setText(selected)
        self.bar.input.setFocus()
        self.bar.input.selectAll()
        self.refresh()

    def close(self):
        if not self._active:
            return
        target = self._current_target()
        self._active = False
        self._timer.stop()
        self._reveal_timer.stop()
        self._clear_highlight()
        self.form.restore_search_expansion()
        self.bar.hide()
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        if target is not None and isValid(target) and target.isVisibleTo(self.form):
            target.setFocus()
        elif self.current_hit and self.current_hit.identity[0] in self.form._module_toggles:
            self.form._module_toggles[self.current_hit.identity[0]].setFocus()
        else:
            self.form.form_scroll.setFocus()

    def reset(self):
        self.close()
        self._timer.stop()
        self._reveal_timer.stop()
        self.bar.input.clear()
        self.hits, self.index = [], -1
        self._update_count(False)

    @property
    def current_hit(self):
        return self.hits[self.index] if 0 <= self.index < len(self.hits) else None

    def _query_changed(self):
        self._new_query = True
        self._reveal_timer.stop()
        self._clear_highlight()
        if self._active:
            self._timer.start()

    def _before_rebuild(self):
        self._reveal_timer.stop()
        self._clear_highlight()

    def _content_changed(self):
        if self._active:
            self._reveal_timer.stop()
            self._clear_highlight()
            self._timer.start()

    def refresh(self):
        self._timer.stop()
        if not self._active:
            return
        previous = None if self._new_query else self.current_hit
        previous_index = self.index
        navigate = self._new_query
        self._new_query = False
        self._clear_highlight()
        query = self.bar.input.text()
        hits, limited = [], False
        if query:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
            for identity, widget in self.form.search_targets():
                for match in pattern.finditer(widget_text(widget)):
                    if len(hits) >= self.MAX_HITS:
                        limited = True
                        break
                    hits.append(CanonFindHit(identity, match.start(), match.end()))
                if limited:
                    break
        self.hits = hits
        self.index = min(previous_index, len(hits) - 1) if hits and previous else (0 if hits else -1)
        if previous:
            same = [(i, abs(hit.start - previous.start)) for i, hit in enumerate(hits)
                    if hit.identity == previous.identity]
            if same:
                self.index = min(same, key=lambda item: item[1])[0]
        self._update_count(limited)
        if not query:
            self.form.restore_search_expansion()
        if navigate:
            self._reveal_current()
            self._reveal_timer.start(0)
        else:
            self._highlight_current()

    def _update_count(self, limited):
        self._limited = limited
        query = self.bar.input.text()
        text = (f"{self.index + 1} / {len(self.hits)}{'+' if limited else ''}" if self.hits else
                "未找到" if query else "输入查找内容")
        self.bar.count.setText(text)
        self.bar.count.setAccessibleName(f"资料查找结果：{text}")
        self.bar.count.setToolTip(f"结果较多，显示前{self.MAX_HITS}处命中。" if limited else
                                  "在当前草稿中查找；到达末项后循环。")
        self.bar.previous.setEnabled(bool(self.hits))
        self.bar.next.setEnabled(bool(self.hits))

    def next(self):
        self._move(1)

    def previous(self):
        self._move(-1)

    def _move(self, step):
        pending_query = self._new_query
        if self._timer.isActive():
            self.refresh()
        if not self.hits:
            return
        if not pending_query:
            self.index = (self.index + step) % len(self.hits)
        elif step < 0:
            self.index = len(self.hits) - 1
        self._update_count(self._limited)
        self._reveal_current()
        self._reveal_timer.start(0)

    def _current_target(self):
        if self.current_hit is None:
            return None
        return dict(self.form.search_targets()).get(self.current_hit.identity)

    def _reveal_current(self):
        if not self._active or self.current_hit is None:
            return
        target = self._current_target()
        if target is None:
            self._content_changed()
            return
        identity = self.current_hit.identity[0]
        self.form.reveal_search_module(identity)
        self.form._navigation_timer.stop()
        self.form._navigation_target = identity or ""
        self.form._set_current_module(identity or "")
        self.form.form_scroll.widget().layout().activate()
        self._highlight_current(navigate=True)
        # Align the target itself. ensureWidgetVisible can leave the actual match
        # below the viewport when a newly expanded editor changes its height.
        content = self.form.form_scroll.widget()
        top = target.mapTo(content, QPoint(0, 0)).y()
        toggle = self.form._module_toggles.get(identity)
        if toggle is not None:
            heading_top = toggle.parentWidget().mapTo(content, QPoint(0, 0)).y()
            if top + target.height() - heading_top <= self.form.form_scroll.viewport().height() - 24:
                top = heading_top
        self.form.form_scroll.verticalScrollBar().setValue(max(0, top - 12))

    def _highlight_current(self, *, navigate=False):
        self._clear_highlight()
        widget = self._current_target()
        if widget is None:
            return
        hit = self.current_hit
        text = widget_text(widget)
        start, end = utf16_position(text, hit.start), utf16_position(text, hit.end)
        self._marked = widget
        widget.setProperty("canonFindCurrent", True)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        self.form._search_navigating = True
        try:
            if isinstance(widget, QPlainTextEdit):
                cursor = QTextCursor(widget.document())
                cursor.setPosition(start)
                if navigate or not widget.hasFocus():
                    original = widget.textCursor()
                    self._old_cursor = (text, original, start)
                    widget.setTextCursor(cursor)
                    widget.ensureCursorVisible()
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(cursor)
                selection.cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
                colors = colors_for(self.form._theme_config)
                selection.format.setBackground(QColor(colors["primary_color"]))
                selection.format.setForeground(QColor(colors["primary_text_color"]))
                widget.setExtraSelections([selection])
            elif isinstance(widget, QLineEdit):
                if navigate or not widget.hasFocus():
                    self._old_cursor = (text, widget.cursorPosition(), widget.selectionStart(),
                                        len(widget.selectedText().encode("utf-16-le")) // 2, start, end)
                    widget.setSelection(start, end - start)
        finally:
            self.form._search_navigating = False

    def _clear_highlight(self):
        widget, cursor = self._marked, self._old_cursor
        self._marked, self._old_cursor = None, None
        if widget is None or not isValid(widget):
            return
        self.form._search_navigating = True
        try:
            if isinstance(widget, QPlainTextEdit):
                widget.setExtraSelections([])
                if (cursor and widget.toPlainText() == cursor[0]
                        and widget.textCursor().position() == cursor[2]
                        and not widget.textCursor().hasSelection()):
                    widget.setTextCursor(cursor[1])
            elif isinstance(widget, QLineEdit) and cursor and widget.text() == cursor[0]:
                if widget.selectionStart() == cursor[4] and widget.cursorPosition() == cursor[5]:
                    if cursor[2] >= 0:
                        anchor = cursor[2] + cursor[3] if cursor[1] == cursor[2] else cursor[2]
                        widget.setSelection(anchor, cursor[1] - anchor)
                    else:
                        widget.deselect()
                        widget.setCursorPosition(cursor[1])
        finally:
            self.form._search_navigating = False
        widget.setProperty("canonFindCurrent", False)
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()
