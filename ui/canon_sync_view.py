"""Read-only presentation of a managed block; never rewrite or normalize its source."""
from __future__ import annotations

import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QFormLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout, QWidget)

from core.character_card_sync import FIELD_SPECS, MANAGED_STATE_END, MANAGED_STATE_START


def display_fields(source: str) -> dict[str, str] | None:
    """Return every recognized value verbatim, or None if any content is ambiguous."""
    if not source.strip():
        return {}
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if (source.count(MANAGED_STATE_START) != 1 or source.count(MANAGED_STATE_END) != 1
            or lines[0] != MANAGED_STATE_START or lines[-1] != MANAGED_STATE_END):
        return None
    labels = {label: field for field, label in FIELD_SPECS}
    labels.update({"本次同步截止章节": "as_of_chapter", "状态截止章节": "as_of_chapter"})
    fields = {}
    for line in lines[1:-1]:
        match = re.fullmatch(r"-\s*([^：:]+)[：:]\s*(.*)", line)
        if match is None:
            return None
        key = labels.get(match[1].strip())
        if key is None or key in fields:
            return None
        fields[key] = match[2].strip()
    return fields


def readonly_label(text: str, name: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setMinimumWidth(0)
    policy = label.sizePolicy()
    policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
    label.setSizePolicy(policy)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse
                                  | Qt.TextInteractionFlag.TextSelectableByKeyboard)
    return label


class CanonSyncView(QWidget):
    sync_requested = Signal()
    chapter_requested = Signal(str)

    def __init__(self, source: str, project=None, parent=None):
        super().__init__(parent)
        self.setObjectName("canonSyncView")
        self._source = source
        self._has_project = project is not None
        self.value_labels: dict[str, QLabel] = {}
        self.chapter_button = None
        self.raw_button = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        fields = display_fields(source)
        if fields is None:
            warning = readonly_label("同步区包含无法完整识别的内容，请查看原文核对。", "saveErrorMessage")
            layout.addWidget(warning)
            self.raw_button = QPushButton("查看同步区原文")
            self.raw_button.clicked.connect(self.show_raw_source)
            layout.addWidget(self.raw_button, alignment=Qt.AlignmentFlag.AlignLeft)
        else:
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setHorizontalSpacing(16)
            form.setVerticalSpacing(10)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignTop)
            for key, heading in (("as_of_chapter", "截止章节"), *FIELD_SPECS):
                value = fields.get(key) or "未记录"
                chapter_id = None
                if key == "as_of_chapter" and value != "未记录":
                    value, chapter_id = self._chapter_display(project, value)
                label = readonly_label(value, "canonSyncValue")
                label.setAccessibleName(f"{heading}：{value}")
                self.value_labels[key] = label
                caption = QLabel(heading)
                caption.setObjectName("canonSyncLabel")
                caption.setTextFormat(Qt.TextFormat.PlainText)
                form.addRow(caption, label)
                if chapter_id is not None:
                    self.chapter_button = QPushButton("打开章节")
                    self.chapter_button.setAccessibleName(f"打开截止章节：{value}")
                    self.chapter_button.clicked.connect(
                        lambda _checked=False, target=chapter_id: self.chapter_requested.emit(target))
                    row = QHBoxLayout()
                    row.setContentsMargins(0, 0, 0, 0)
                    row.addWidget(self.chapter_button)
                    row.addStretch()
                    form.addRow("", row)
            layout.addLayout(form)
        caption = readonly_label("自动同步内容只读；更新前将先展示预览，由你确认采用的字段。", "canonCaption")
        layout.addWidget(caption)
        self.sync_button = QPushButton("同步角色档案…")
        self.sync_button.setObjectName("canonSyncButton")
        self.sync_button.clicked.connect(self.sync_requested)
        layout.addWidget(self.sync_button, alignment=Qt.AlignmentFlag.AlignLeft)
        self.set_sync_available(True)

    @staticmethod
    def _chapter_display(project, chapter_id: str) -> tuple[str, str | None]:
        if project is None or not hasattr(project, "list_chapters"):
            return f"尚未打开项目（{chapter_id}）", None
        try:
            path = next((p for p in project.list_chapters() if p.stem == chapter_id), None)
            if (path is None or not path.is_file()
                    or path.resolve().parent != project.chapters_dir.resolve()):
                return f"章节不存在（{chapter_id}）", None
            return project.load_chapter(path.stem).title, path.stem
        except (OSError, UnicodeError, ValueError):
            return f"章节无法读取（{chapter_id}）", None

    def set_sync_available(self, available: bool, reason: str = "") -> None:
        self.sync_button.setEnabled(self._has_project and available)
        self.sync_button.setToolTip(reason if not available else
            "先预览并确认更新字段" if self._has_project else "请在项目中打开角色资料后同步")

    def show_raw_source(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("同步区原文（只读）")
        dialog.resize(720, 480)
        layout = QVBoxLayout(dialog)
        viewer = QPlainTextEdit()
        viewer.setAccessibleName("同步区原文，只读")
        viewer.setReadOnly(True)
        viewer.setPlainText(self._source)
        layout.addWidget(viewer)
        close = QPushButton("关闭")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        dialog.exec()
        dialog.deleteLater()
