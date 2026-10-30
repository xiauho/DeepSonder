"""Chapter writing surface: preparation fields above a prose editor."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.chapter_document import ChapterDocument, ChapterEdits, rejection_reason
from ui.elided_label import ElidedLabel
from ui.prose_edit import ProseTextEdit
from ui.prose_width import DEFAULT_PROSE_WIDTH, MIN_PROSE_WIDTH, MAX_PROSE_WIDTH, PROSE_WIDTH_MODES, ProseWidthHandle


_FIELDS = (
    ("goal", "本章目标", "这一章结束时，读者应知道什么"),
    ("conflict", "核心冲突", "谁和谁发生了什么对立"),
    ("hook", "章节钩子", "章末留下什么未解的事"),
)


class ChapterWritingPage(QWidget):
    """Editable chapter view. Callers own the Markdown and decide when to save it."""

    changed = Signal()
    reading_width_changed = Signal()
    width_value_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("chapterWritingPage")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._loading = False
        self._outline_mode = "fields"
        self._author_present = False
        self._prep_expanded = True
        self._width_mode = "recommended"
        self._custom_width = DEFAULT_PROSE_WIDTH
        self._width_drag = None
        self._column_timer = QTimer(self)
        self._column_timer.setSingleShot(True)
        self._column_timer.timeout.connect(self._layout_reading_column)
        self._extra_fields: list[tuple[str, QPlainTextEdit]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.prep_title = QLabel("本章准备")
        self.prep_title.setObjectName("sectionTitle")
        self.summary = ElidedLabel("")
        self.summary.setObjectName("mutedLabel")
        self.summary.setMinimumWidth(0)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.prep_toggle = QToolButton()
        self.prep_toggle.setObjectName("editorModeButton")
        self.prep_toggle.setText("收起")
        self.prep_toggle.setCheckable(True)
        self.prep_toggle.setChecked(True)
        self.prep_toggle.setAccessibleName("展开或收起本章准备")
        self.prep_toggle.clicked.connect(self._toggle_prep)

        self.prep = QWidget()
        prep_layout = QVBoxLayout(self.prep)
        prep_layout.setContentsMargins(0, 0, 0, 0)
        prep_layout.setSpacing(8)
        self.fields: dict[str, QLineEdit] = {}
        for key, label, placeholder in _FIELDS:
            self.fields[key] = self._line(prep_layout, label, placeholder)
        self.outline_rest = self._text(prep_layout, "其他大纲", "这三条之外还想记住的安排", 72)
        self.outline_text = self._text(prep_layout, "大纲", "这一章打算怎么推进", 96)
        self.plot_brief = self._text(prep_layout, "剧情简写", "用自己的话写下这一章要发生什么", 96)
        self.guide = QLabel("可以先写这一章要发生什么，也可以直接写正文。")
        self.guide.setObjectName("mutedLabel")
        self.guide.setWordWrap(True)
        prep_layout.addWidget(self.guide)
        self.prep_bar = QWidget()
        bar = QHBoxLayout(self.prep_bar)
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        bar.addWidget(self.prep_title)
        bar.addWidget(self.summary, 1)
        bar.addWidget(self.prep_toggle)
        layout.addWidget(self.prep_bar)
        layout.addWidget(self.prep)

        self.notice = QLabel("")
        self.notice.setObjectName("mutedLabel")
        self.notice.setWordWrap(True)
        self.notice.hide()
        layout.addWidget(self.notice)

        self.body_edit = ProseTextEdit()
        self.body_edit.setObjectName("writingEditor")
        self.body_edit.setProperty("chapterProse", True)
        self.body_edit.installEventFilter(self)
        self.body_edit.viewport().installEventFilter(self)
        self.body_edit.verticalScrollBar().installEventFilter(self)
        self.body_edit.setPlaceholderText("从这里开始写这一章。")
        self.body_edit.setAccessibleName("正文")
        layout.addWidget(self.body_edit, 1)

        self.extras_host = QWidget()
        self.extras_layout = QVBoxLayout(self.extras_host)
        self.extras_layout.setContentsMargins(0, 0, 0, 0)
        self.extras_layout.setSpacing(8)
        self.extras_host.hide()
        layout.addWidget(self.extras_host)

        self.author_note = self._text(layout, "作者备注", "只留给自己的提醒，不会写成正文", 88)
        self.outline_rest.hide()
        self._outline_rest_caption.hide()
        self.outline_text.hide()
        self._outline_text_caption.hide()
        self.author_note.hide()
        self._author_caption.hide()

        self.body_edit.textChanged.connect(self._emit)
        self.body_edit.textChanged.connect(self._refresh_guide)
        for field in self.fields.values():
            field.textChanged.connect(self._emit)
            field.textChanged.connect(self._refresh_summary)
        self.outline_rest.textChanged.connect(self._emit)
        self.outline_text.textChanged.connect(self._emit)
        self.outline_text.textChanged.connect(self._refresh_summary)
        self.plot_brief.textChanged.connect(self._emit)
        self.plot_brief.textChanged.connect(self._refresh_summary)
        self.author_note.textChanged.connect(self._emit)
        self.width_handles = {}
        for side in ("left", "right"):
            handle = ProseWidthHandle(side, self)
            handle.drag_started.connect(self._start_width_drag)
            handle.dragged.connect(lambda delta, edge=side: self._drag_width(edge, delta))
            handle.drag_finished.connect(self._finish_width_drag)
            handle.drag_cancelled.connect(self._cancel_width_drag)
            handle.stepped.connect(self._step_width)
            handle.reset_requested.connect(lambda: self.set_reading_width("recommended"))
            handle.clicked.connect(self.width_value_requested)
            self.width_handles[side] = handle

    def comfortable_width(self) -> bool:
        return self._width_mode != "fill"

    def set_comfortable_width(self, enabled: bool) -> None:
        """Reflow existing widgets without touching document content or history."""
        self.set_reading_width("recommended" if enabled else "fill")

    def reading_width_preferences(self) -> dict:
        return {"mode": self._width_mode, "custom_width": self._custom_width}

    def recommended_prose_width(self) -> int:
        return max(680, min(820, self.body_edit.fontMetrics().horizontalAdvance("中") * 42))

    def set_reading_width(self, mode: str, custom_width: int | None = None) -> None:
        if not isinstance(mode, str) or mode not in PROSE_WIDTH_MODES:
            raise ValueError("unknown reading-width mode")
        if custom_width is not None and type(custom_width) is not int:
            raise ValueError("custom prose width must be an integer")
        before = self.reading_width_preferences()
        self._width_mode = mode
        if custom_width is not None:
            self._custom_width = max(MIN_PROSE_WIDTH, min(MAX_PROSE_WIDTH, custom_width))
        self._layout_reading_column()
        if before != self.reading_width_preferences():
            self.reading_width_changed.emit()

    def _start_width_drag(self) -> None:
        self._width_drag = (self.reading_width_preferences(), max(1, self.body_edit.width() - 64))

    def _drag_width(self, side: str, delta: int) -> None:
        if self._width_drag is not None:
            self.set_reading_width("custom", self._width_drag[1] + delta * (2 if side == "right" else -2))

    def _finish_width_drag(self) -> None:
        self._width_drag = None

    def _cancel_width_drag(self) -> None:
        if self._width_drag is not None:
            previous, _width = self._width_drag
            self._width_drag = None
            self.set_reading_width(previous["mode"], previous["custom_width"])

    def _step_width(self, delta: int) -> None:
        width = self._custom_width if self._width_mode == "custom" else max(1, self.body_edit.width() - 64)
        self.set_reading_width("custom", width + delta)

    def _layout_reading_column(self) -> None:
        # Bound the reading measure while respecting the author's font choice.
        measure = self._custom_width if self._width_mode == "custom" else self.recommended_prose_width()
        column = measure + 64  # Match the prose editor's horizontal padding.
        gutter = max(0, (self.contentsRect().width() - column) // 2) if self.comfortable_width() else 0
        if self.layout().contentsMargins().left() != gutter:
            self.layout().setContentsMargins(gutter, 0, gutter, 0)
        self.layout().activate()
        self._position_width_handles()

    def _position_width_handles(self) -> None:
        if not hasattr(self, "width_handles"):
            return
        edit = self.body_edit
        viewport = edit.viewport()
        top = viewport.mapTo(self, QPoint(0, 0))
        scrollbar = edit.verticalScrollBar()
        right_edge = top.x() + viewport.width()
        if scrollbar.isVisibleTo(edit):
            right_edge = scrollbar.mapTo(self, QPoint(0, 0)).x() + scrollbar.width()
        for side, handle in self.width_handles.items():
            # Stay in the existing 32px padding, outside text and scrollbars.
            x = edit.x() + 8 if side == "left" else right_edge + 8
            x = max(edit.x(), min(x, edit.geometry().right() - 13))
            handle.setGeometry(x, top.y(), 14, max(1, viewport.height()))
            mode = {"recommended": "推荐", "custom": "自定义", "fill": "铺满"}[self._width_mode]
            handle.setAccessibleDescription(f"当前模式：{mode}；可见行宽约 {max(1, edit.width() - 64)} px。方向键调整，Enter 输入数值。")
            handle.setVisible(edit.isVisibleTo(self))
            handle.raise_()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_reading_column()

    def eventFilter(self, watched, event):
        if watched is self.body_edit and event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._column_timer.start(0)
        elif watched in (self.body_edit, self.body_edit.viewport(), self.body_edit.verticalScrollBar()) and event.type() in (
                QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.Hide):
            self._column_timer.start(0)
        return super().eventFilter(watched, event)

    def load(self, document: ChapterDocument, *, reset_prep: bool) -> None:
        self._loading = True
        self._outline_mode = document.outline_mode
        self._author_present = document.author_note is not None
        verbatim = document.outline_mode == "verbatim"
        self.prep_bar.setVisible(not verbatim)
        self.prep.setVisible(not verbatim and self._prep_expanded)
        self.notice.setText(rejection_reason(document.raw) if verbatim else "")
        self.notice.setVisible(verbatim)
        fields_mode = document.outline_mode == "fields" or document.outline_mode == "absent"
        for key, field in self.fields.items():
            field.setText(getattr(document, key))
            field.setVisible(fields_mode)
            caption = getattr(self, f"_{key}_caption")
            caption.setVisible(fields_mode)
        self.outline_rest.setPlainText(document.outline_rest)
        show_rest = fields_mode and bool(document.outline_rest.strip())
        self.outline_rest.setVisible(show_rest)
        self._outline_rest_caption.setVisible(show_rest)
        self.outline_text.setPlainText(document.outline_text if document.outline_mode == "freeform" else "")
        self.outline_text.setVisible(not fields_mode)
        self._outline_text_caption.setVisible(not fields_mode)
        self.plot_brief.setPlainText(document.plot_brief)
        self.body_edit.setPlainText(document.body)
        self.author_note.setPlainText(document.author_note or "")
        self.author_note.setVisible(self._author_present)
        self._author_caption.setVisible(self._author_present)
        self._set_extras(document.extras)
        if reset_prep and not verbatim:
            self.set_prep_expanded(not document.body.strip())
        self._loading = False
        self._refresh_summary()
        self._refresh_guide()

    def edits(self) -> ChapterEdits:
        return ChapterEdits(
            title="",
            goal=self.fields["goal"].text(),
            conflict=self.fields["conflict"].text(),
            hook=self.fields["hook"].text(),
            outline_rest=self.outline_rest.toPlainText(),
            outline_text=self.outline_text.toPlainText(),
            plot_brief=self.plot_brief.toPlainText(),
            body=self.body_edit.toPlainText(),
            author_note=self.author_note.toPlainText() if self._author_present else None,
            extras=tuple((name, field.toPlainText()) for name, field in self._extra_fields),
        )

    def set_prep_expanded(self, expanded: bool) -> None:
        if self._outline_mode == "verbatim":
            self.prep_bar.hide()
            self.prep.hide()
            return
        self._prep_expanded = expanded
        self.prep_bar.show()
        self.prep.setVisible(expanded)
        self.prep_toggle.setChecked(expanded)
        self.prep_toggle.setText("收起" if expanded else "展开")
        self.summary.setVisible(not expanded)

    def focus_body_end(self) -> None:
        cursor = self.body_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.body_edit.setTextCursor(cursor)
        self.body_edit.ensureCursorVisible()

    def _line(self, layout: QVBoxLayout, label: str, placeholder: str) -> QLineEdit:
        caption = QLabel(label)
        caption.setObjectName("fieldLabel")
        field = QLineEdit()
        field.setPlaceholderText(placeholder)
        field.setAccessibleName(label)
        field.setClearButtonEnabled(True)
        layout.addWidget(caption)
        layout.addWidget(field)
        key = {item[1]: item[0] for item in _FIELDS}[label]
        setattr(self, f"_{key}_caption", caption)
        return field

    def _text(self, layout, label: str, placeholder: str, height: int) -> QPlainTextEdit:
        caption = QLabel(label)
        caption.setObjectName("fieldLabel")
        field = QPlainTextEdit()
        field.setObjectName("storyPlanField")
        field.setPlaceholderText(placeholder)
        field.setAccessibleName(label)
        field.setMinimumHeight(height)
        field.setMaximumHeight(height + 48)
        layout.addWidget(caption)
        layout.addWidget(field)
        attr = {
            "其他大纲": "_outline_rest_caption",
            "大纲": "_outline_text_caption",
            "剧情简写": "_plot_caption",
            "作者备注": "_author_caption",
        }[label]
        setattr(self, attr, caption)
        return field

    def _set_extras(self, extras: tuple[tuple[str, str], ...]) -> None:
        while self.extras_layout.count():
            item = self.extras_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._extra_fields = []
        for name, text in extras:
            caption = QLabel(name)
            caption.setObjectName("fieldLabel")
            field = QPlainTextEdit()
            field.setObjectName("storyPlanField")
            field.setAccessibleName(name)
            field.setPlainText(text)
            field.setMinimumHeight(88)
            field.textChanged.connect(self._emit)
            self.extras_layout.addWidget(caption)
            self.extras_layout.addWidget(field)
            self._extra_fields.append((name, field))
        self.extras_host.setVisible(bool(extras))

    def _toggle_prep(self) -> None:
        self.set_prep_expanded(self.prep_toggle.isChecked())

    def _refresh_summary(self) -> None:
        if self._outline_mode == "freeform":
            outline = "大纲已填" if self.outline_text.toPlainText().strip() else "大纲未填"
            detail = outline
        else:
            marks = [
                ("目标", self.fields["goal"].text()),
                ("冲突", self.fields["conflict"].text()),
                ("钩子", self.fields["hook"].text()),
            ]
            outline = f"大纲 {sum(bool(value.strip()) for _, value in marks)}/3 项有内容"
            detail = " · ".join(f"{name}{'有内容' if value.strip() else '空白'}" for name, value in marks)
        plot = "剧情简写已填" if self.plot_brief.toPlainText().strip() else "剧情简写未填"
        self.summary.setText(f"{outline} · {plot}")
        self.summary.setToolTip(f"{detail} · {plot}；各项可选，不代表大纲完整性。")

    def _refresh_guide(self) -> None:
        self.guide.setVisible(
            self._outline_mode != "verbatim"
            and self._prep_expanded
            and not self.body_edit.toPlainText().strip()
        )

    def _emit(self) -> None:
        if not self._loading:
            self.changed.emit()
