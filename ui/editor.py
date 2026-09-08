from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.chapter_sections import chapter_body_bounds, chapter_body_text
from core.project import NovelProject
from core.storage import atomic_write_text
from core.text_metrics import count_content_chars
from ui.icons import set_button_icon
from ui.theme import document_css


_PARAGRAPH_RE = re.compile(r"\n\s*\n")
FIND_DEBOUNCE_MS = 240
STATS_DEBOUNCE_MS = 180
MAX_FIND_HIGHLIGHTS = 200
MAX_FIND_MATCHES_FOR_HIGHLIGHT = 1000
LARGE_DOCUMENT_CHARS = 200_000
_NOVALIST_PREVIEW_COMMENT_RE = re.compile(
    r"(?m)^[ \t]*<!--[ \t]*novalist:[^\r\n]*?-->[ \t]*(?:\r?\n|$)"
)


def calculate_editor_stats(text: str) -> str:
    """Return the display label for one immutable editor text snapshot."""
    text = str(text or "")
    total = count_content_chars(text)
    paragraphs = len([part for part in _PARAGRAPH_RE.split(text) if part.strip()])
    minutes = max(1, round(total / 400)) if total else 0
    label = f"{total:,} 字 · {paragraphs} 段"
    if total:
        label += f" · 约 {minutes} 分钟阅读"
    return label


def find_highlight_config(document_length: int, match_count: int) -> tuple[int, str]:
    """Return the highlight limit and user-facing suffix for find results."""
    document_length = max(0, int(document_length))
    match_count = max(0, int(match_count))
    if document_length >= LARGE_DOCUMENT_CHARS:
        return 0, " · 大文档暂不高亮"
    if match_count > MAX_FIND_MATCHES_FOR_HIGHLIGHT:
        return 0, " · 匹配过多，仅显示数量"
    limit = min(match_count, MAX_FIND_HIGHLIGHTS)
    suffix = f" · 仅显示前 {limit} 处" if match_count > limit else ""
    return limit, suffix


def markdown_preview_source(text: str) -> str:
    """Remove app-only comments from a display copy of Markdown source."""
    return _NOVALIST_PREVIEW_COMMENT_RE.sub("", str(text or ""))


class ExternalFileChangedError(RuntimeError):
    """Raised when the file changed after it was loaded into the editor."""

    def __init__(self, path: Path):
        self.path = Path(path)
        super().__init__(f"文件在编辑期间发生了外部修改：{self.path}")


class MarkdownPreview(QTextBrowser):
    """Read-only Markdown surface that never retrieves remote resources."""

    _BLOCKED_SCHEMES = frozenset({"http", "https", "ftp"})

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)

    def loadResource(self, resource_type: int, name: QUrl):  # noqa: N802 - Qt API
        if name.scheme().casefold() in self._BLOCKED_SCHEMES:
            return None
        return super().loadResource(resource_type, name)


class Editor(QWidget):
    """Focused Markdown editor with document state, stats and find/replace."""

    dirty_changed = Signal(bool)
    stats_changed = Signal(str)
    file_saved = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_path: str | None = None
        self._current_category = ""
        self._dirty = False
        self._loading = False
        self._loaded_file_revision: tuple[int, int, int] | None = None
        self._stats_revision: int | None = None
        self._view_mode = "source"
        self._theme_config: dict = {"theme": "light"}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 12)
        layout.setSpacing(10)

        header = QFrame()
        header.setObjectName("editorHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(2, 0, 2, 0)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        self.title_label = QLabel("开始你的故事")
        self.title_label.setObjectName("documentTitle")
        self.path_label = QLabel("从左侧选择文件，或创建一个新章节")
        self.path_label.setObjectName("mutedLabel")
        title_box.addWidget(self.title_label)
        title_box.addWidget(self.path_label)
        header_layout.addLayout(title_box, 1)

        self.source_button = QToolButton()
        self.source_button.setText("源码")
        self.source_button.setObjectName("editorModeButton")
        self.source_button.setCheckable(True)
        self.preview_button = QToolButton()
        self.preview_button.setText("预览")
        self.preview_button.setObjectName("editorModeButton")
        self.preview_button.setCheckable(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self.source_button)
        self._mode_group.addButton(self.preview_button)
        self.source_button.setChecked(True)
        self.source_button.clicked.connect(lambda: self.set_view_mode("source"))
        self.preview_button.clicked.connect(lambda: self.set_view_mode("preview"))
        header_layout.addWidget(self.source_button)
        header_layout.addWidget(self.preview_button)

        self.exit_focus_button = QPushButton("退出专注  Esc")
        self.exit_focus_button.setObjectName("focusExitButton")
        self.exit_focus_button.setToolTip("退出专注模式（Esc）")
        self.exit_focus_button.hide()
        header_layout.addWidget(
            self.exit_focus_button, 0, Qt.AlignmentFlag.AlignVCenter
        )

        self.dirty_badge = QLabel("已保存")
        self.dirty_badge.setObjectName("savedBadge")
        header_layout.addWidget(self.dirty_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(header)

        self.find_bar = QFrame()
        self.find_bar.setObjectName("findBar")
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(10, 8, 10, 8)
        find_layout.setSpacing(7)
        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("查找内容")
        self.find_input.setClearButtonEnabled(True)
        self.replace_input = QLineEdit()
        self.replace_input.setPlaceholderText("替换为…")
        self.replace_input.setClearButtonEnabled(True)
        self.match_label = QLabel("")
        self.match_label.setObjectName("mutedLabel")
        previous_btn = QToolButton()
        previous_btn.setText("上一个")
        next_btn = QToolButton()
        next_btn.setText("下一个")
        replace_btn = QPushButton("替换")
        replace_all_btn = QPushButton("全部替换")
        close_btn = QToolButton()
        set_button_icon(close_btn, "close", size=17)
        close_btn.setToolTip("关闭查找")
        find_layout.addWidget(self.find_input, 2)
        find_layout.addWidget(self.replace_input, 2)
        find_layout.addWidget(self.match_label)
        find_layout.addWidget(previous_btn)
        find_layout.addWidget(next_btn)
        find_layout.addWidget(replace_btn)
        find_layout.addWidget(replace_all_btn)
        find_layout.addWidget(close_btn)
        self.find_bar.hide()
        layout.addWidget(self.find_bar)

        self.text_edit = QPlainTextEdit()
        self.text_edit.setObjectName("writingEditor")
        self.text_edit.setPlaceholderText(
            "在这里写下故事。\n\n提示：章节建议保留「## 大纲」「## 剧情简写」和「## 正文」标记，AI 创作会据此理解你的写作意图。"
        )
        self.text_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text_edit.setTabStopDistance(32.0)
        self.preview_browser = MarkdownPreview()
        self.preview_browser.setObjectName("markdownPreview")
        self.preview_browser.setToolTip("只读 Markdown 预览；保存仍以源码内容为准")
        self.preview_browser.document().setDefaultStyleSheet(
            document_css(self._theme_config)
        )
        self.editor_stack = QStackedWidget()
        self.editor_stack.setObjectName("editorViewStack")
        self.editor_stack.addWidget(self.text_edit)
        self.editor_stack.addWidget(self.preview_browser)
        layout.addWidget(self.editor_stack, 1)

        footer = QFrame()
        footer.setObjectName("editorFooter")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(3, 0, 3, 0)
        self.stats_label = QLabel("0 字 · 0 段")
        self.stats_label.setObjectName("mutedLabel")
        self.cursor_label = QLabel("第 1 行，第 1 列")
        self.cursor_label.setObjectName("mutedLabel")
        footer_layout.addWidget(self.stats_label)
        footer_layout.addStretch(1)
        footer_layout.addWidget(QLabel("Markdown"))
        footer_layout.addWidget(self.cursor_label)
        layout.addWidget(footer)

        self.text_edit.textChanged.connect(self._on_text_changed)
        self.text_edit.cursorPositionChanged.connect(self._update_cursor_status)
        self.find_input.textChanged.connect(self._schedule_match_count)
        self.find_input.returnPressed.connect(self.find_next)
        previous_btn.clicked.connect(self.find_previous)
        next_btn.clicked.connect(self.find_next)
        replace_btn.clicked.connect(self.replace_current)
        replace_all_btn.clicked.connect(self.replace_all)
        close_btn.clicked.connect(self.hide_find)

        self._stats_timer = QTimer(self)
        self._stats_timer.setSingleShot(True)
        self._stats_timer.setInterval(STATS_DEBOUNCE_MS)
        self._stats_timer.timeout.connect(self._update_stats)

        self._find_timer = QTimer(self)
        self._find_timer.setSingleShot(True)
        self._find_timer.setInterval(FIND_DEBOUNCE_MS)
        self._find_timer.timeout.connect(self._update_match_count)

    def set_view_mode(self, mode: str) -> None:
        """Switch between editable source and a read-only in-memory preview."""
        normalized = "preview" if str(mode) == "preview" else "source"
        self._view_mode = normalized
        if normalized == "preview":
            self._find_timer.stop()
            self.find_bar.hide()
            self._clear_find_highlights()
            self._render_preview()
            self.editor_stack.setCurrentWidget(self.preview_browser)
            self.preview_button.setChecked(True)
            self.cursor_label.setText("只读预览")
            self.preview_browser.setFocus()
            return
        self.editor_stack.setCurrentWidget(self.text_edit)
        self.source_button.setChecked(True)
        self._update_cursor_status()
        self.text_edit.setFocus()

    def view_mode(self) -> str:
        return self._view_mode

    def set_theme(self, config: dict | None = None) -> None:
        """Refresh theme-aware document CSS without changing the source."""
        self._theme_config = dict(config or {"theme": "light"})
        self.preview_browser.document().setDefaultStyleSheet(
            document_css(self._theme_config)
        )
        if self._view_mode == "preview":
            self._render_preview()

    def undo(self) -> None:
        self.set_view_mode("source")
        self.text_edit.undo()

    def redo(self) -> None:
        self.set_view_mode("source")
        self.text_edit.redo()

    def _render_preview(self, *, reset_scroll: bool = False) -> None:
        scroll = 0 if reset_scroll else self.preview_browser.verticalScrollBar().value()
        self.preview_browser.setMarkdown(
            markdown_preview_source(self.text_edit.toPlainText())
        )
        self.preview_browser.verticalScrollBar().setValue(scroll)

    def open_file(self, category: str, path_str: str) -> bool:
        path = Path(path_str)
        if not path.exists():
            self.clear_document("文件不存在")
            return False
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            self.clear_document(f"读取失败：{exc}")
            return False

        self._loading = True
        self._current_path = str(path)
        self._current_category = category
        self._loaded_file_revision = self._file_revision(path)
        self.text_edit.setPlainText(content)
        self.text_edit.document().setModified(False)
        self._loading = False
        self._set_dirty(False)
        self.title_label.setText(self._extract_title(content, path))
        self.path_label.setText(f"{category}  /  {path.name}")
        if self._view_mode == "preview":
            self._render_preview(reset_scroll=True)
            self.preview_browser.setFocus()
        else:
            self.text_edit.setFocus()
        self._update_stats()
        return True

    def clear_document(self, message: str = "未打开文件") -> None:
        self._loading = True
        self.text_edit.clear()
        self.preview_browser.clear()
        self._loading = False
        self._current_path = None
        self._current_category = ""
        self._loaded_file_revision = None
        self.title_label.setText(message)
        self.path_label.setText("从左侧选择文件，或创建一个新章节")
        self._set_dirty(False)

    def save(self, *, force: bool = False) -> bool:
        if not self._current_path:
            return False
        path = Path(self._current_path)
        if not force and self.has_external_change():
            raise ExternalFileChangedError(path)
        try:
            atomic_write_text(path, self.text_edit.toPlainText())
        except OSError as exc:
            self.dirty_badge.setText("保存失败")
            self.path_label.setText(f"保存失败：{exc}")
            return False
        self.text_edit.document().setModified(False)
        self._loaded_file_revision = self._file_revision(path)
        self._set_dirty(False)
        self.file_saved.emit(str(path))
        return True

    def has_external_change(self) -> bool:
        """Return whether the loaded file changed outside this editor."""
        if not self._current_path or self._loaded_file_revision is None:
            return False
        return self._file_revision(Path(self._current_path)) != self._loaded_file_revision

    def reload_current_file(self) -> bool:
        """Discard local edits and reload the current file from disk."""
        if not self._current_path:
            return False
        return self.open_file(self._current_category, self._current_path)

    def is_dirty(self) -> bool:
        return self._dirty

    def current_path(self) -> str | None:
        return self._current_path

    def current_category(self) -> str:
        return self._current_category

    @staticmethod
    def _file_revision(path: Path) -> tuple[int, int, int] | None:
        try:
            stat = Path(path).stat()
        except OSError:
            return None
        return stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size

    def current_chapter_id(self) -> str | None:
        if not self._current_path:
            return None
        path = Path(self._current_path)
        return path.stem if self._current_category == "章节" else None

    def append_text(self, text: str) -> None:
        self.set_view_mode("source")
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if self.text_edit.toPlainText().strip():
            cursor.insertText("\n\n" + text.strip() + "\n")
        else:
            cursor.insertText(text.strip() + "\n")
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

    def append_chapter_body(self, text: str) -> None:
        """Append one continuation inside ``## 正文`` as one undoable edit."""
        self.set_view_mode("source")
        continuation = str(text or "").strip()
        if not continuation:
            return
        raw = self.text_edit.toPlainText()
        bounds = chapter_body_bounds(raw)
        cursor = self.text_edit.textCursor()
        cursor.beginEditBlock()
        if bounds is None:
            cursor.movePosition(QTextCursor.MoveOperation.End)
            prefix = "\n\n" if raw.rstrip() else ""
            cursor.insertText(f"{prefix}## 正文\n{continuation}")
        else:
            start, end = bounds
            existing = raw[start:end].strip()
            cursor.setPosition(end)
            cursor.insertText(("\n\n" if existing else "\n") + continuation)
        cursor.endEditBlock()
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

    def replace_chapter_body(self, text: str) -> None:
        """Replace only the current chapter's ``## 正文`` section."""
        self.set_view_mode("source")
        raw = self.text_edit.toPlainText()
        body = str(text or "").strip()
        replacement = NovelProject.replace_chapter_body(raw, body)
        self.text_edit.setPlainText(replacement)
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

    def reveal_range(self, start: int, end: int) -> bool:
        """Select and reveal a zero-based character range in the editor."""
        self.set_view_mode("source")
        document_length = self.text_edit.document().characterCount() - 1
        start = max(0, min(int(start), document_length))
        end = max(start, min(int(end), document_length))
        cursor = self.text_edit.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()
        self.text_edit.setFocus()
        return end > start

    def replace_range_if_matches(
        self,
        start: int,
        end: int,
        expected: str,
        replacement: str,
    ) -> bool:
        """Replace one range only when its current text still matches exactly."""
        self.set_view_mode("source")
        current = self.text_edit.toPlainText()
        start = max(0, int(start))
        end = min(len(current), max(start, int(end)))
        if current[start:end] != str(expected):
            return False
        cursor = self.text_edit.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(str(replacement))
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()
        return True

    def cursor_snapshot(self) -> tuple[int, int]:
        cursor = self.text_edit.textCursor()
        return cursor.position(), cursor.anchor()

    def insert_text_at_snapshot(
        self,
        text: str,
        position: int,
        anchor: int | None = None,
    ) -> None:
        self.set_view_mode("source")
        cursor = self.text_edit.textCursor()
        document_length = self.text_edit.document().characterCount() - 1
        position = max(0, min(int(position), document_length))
        anchor = position if anchor is None else max(0, min(int(anchor), document_length))
        cursor.setPosition(anchor)
        cursor.setPosition(position, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(text.strip())
        self.text_edit.setTextCursor(cursor)
        self.text_edit.ensureCursorVisible()

    def show_find(self) -> None:
        self.set_view_mode("source")
        self.find_bar.show()
        selected = self.text_edit.textCursor().selectedText()
        if selected and "\u2029" not in selected and len(selected) < 100:
            self.find_input.setText(selected)
        self._schedule_match_count()
        self.find_input.setFocus()
        self.find_input.selectAll()

    def hide_find(self) -> None:
        self._find_timer.stop()
        self.find_bar.hide()
        self._clear_find_highlights()
        self.text_edit.setFocus()

    def find_next(self) -> None:
        needle = self.find_input.text()
        if not needle:
            return
        if not self.text_edit.find(needle):
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.text_edit.setTextCursor(cursor)
            self.text_edit.find(needle)

    def find_previous(self) -> None:
        needle = self.find_input.text()
        if not needle:
            return
        backward = QTextDocument.FindFlag.FindBackward
        if not self.text_edit.find(needle, backward):
            cursor = self.text_edit.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            self.text_edit.setTextCursor(cursor)
            self.text_edit.find(needle, backward)

    def replace_current(self) -> None:
        cursor = self.text_edit.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == self.find_input.text():
            cursor.insertText(self.replace_input.text())
        self.find_next()
        self._schedule_match_count()

    def replace_all(self) -> None:
        needle = self.find_input.text()
        if not needle:
            return
        text = self.text_edit.toPlainText()
        count = text.count(needle)
        if count:
            cursor = self.text_edit.textCursor()
            cursor.beginEditBlock()
            cursor.select(QTextCursor.SelectionType.Document)
            cursor.insertText(text.replace(needle, self.replace_input.text()))
            cursor.endEditBlock()
        self.match_label.setText(f"已替换 {count} 处")

    def _on_text_changed(self) -> None:
        if not self._loading:
            self._set_dirty(True)
        self._stats_timer.start()
        if self.find_bar.isVisible():
            self._schedule_match_count()

    def _set_dirty(self, dirty: bool) -> None:
        changed = dirty != self._dirty
        self._dirty = dirty
        self.dirty_badge.setText("未保存" if dirty else "已保存")
        self.dirty_badge.setObjectName("dirtyBadge" if dirty else "savedBadge")
        self.dirty_badge.style().unpolish(self.dirty_badge)
        self.dirty_badge.style().polish(self.dirty_badge)
        if changed:
            self.dirty_changed.emit(dirty)

    def _update_stats(self) -> None:
        revision = int(self.text_edit.document().revision())
        if self._stats_revision == revision:
            return
        source = self.text_edit.toPlainText()
        measured = chapter_body_text(source) if self._current_category == "章节" else source
        label = calculate_editor_stats(measured)
        self._stats_revision = revision
        self.stats_label.setText(label)
        self.stats_changed.emit(label)

    def _update_cursor_status(self) -> None:
        cursor = self.text_edit.textCursor()
        self.cursor_label.setText(
            f"第 {cursor.blockNumber() + 1} 行，第 {cursor.positionInBlock() + 1} 列"
        )

    def _schedule_match_count(self) -> None:
        self._find_timer.start()

    def _update_match_count(self) -> None:
        needle = self.find_input.text()
        if not needle:
            self.match_label.clear()
            self._clear_find_highlights()
            return

        text = self.text_edit.toPlainText()
        count = text.count(needle)
        highlight_limit, suffix = find_highlight_config(len(text), count)
        self.match_label.setText(f"{count} 处{suffix}")
        selections = []
        document = self.text_edit.document()
        cursor = QTextCursor(document)
        fmt = QTextCharFormat()
        fmt.setBackground(QColor("#C08A2E"))
        while len(selections) < highlight_limit:
            cursor = document.find(needle, cursor)
            if cursor.isNull():
                break
            selection = QTextEdit.ExtraSelection()
            selection.cursor = cursor
            selection.format = fmt
            selections.append(selection)
        self.text_edit.setExtraSelections(selections)

    def _clear_find_highlights(self) -> None:
        self.text_edit.setExtraSelections([])

    @staticmethod
    def _extract_title(content: str, path: Path) -> str:
        for line in content.splitlines():
            if line.startswith("# "):
                return line[2:].strip() or path.stem
        return path.stem
