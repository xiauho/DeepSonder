"""Dialogs for creating and editing author-owned foreshadowing notes."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
)


class ForeshadowingEditorDialog(QDialog):
    """Edit author-controlled fields while keeping lifecycle fields read-only."""

    def __init__(self, note: dict | None = None, parent=None):
        super().__init__(parent)
        self.note = dict(note or {})
        self.setWindowTitle("编辑伏笔" if note else "新建伏笔")
        self.resize(560, 520)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        hint = QLabel(
            "伏笔内容由作者维护；最近出现章节、回收章节和当前状态将在后续 AI 任务确认后自动更新。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        root.addWidget(hint)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.title = QLineEdit(str(self.note.get("title") or ""))
        self.title.setPlaceholderText("例如：残缺古剑的来历")
        self.description = QPlainTextEdit(str(self.note.get("note") or ""))
        self.description.setPlaceholderText("记录伏笔内容、后续方向和作者自己的判断")
        self.description.setMinimumHeight(100)
        self.first_seen = QLineEdit(str(self.note.get("first_seen_chapter") or ""))
        self.first_seen.setPlaceholderText("例如：chapter_01")
        self.planned_resolution = QLineEdit(
            str(self.note.get("planned_resolution_chapter") or "")
        )
        self.planned_resolution.setPlaceholderText("可选，例如：chapter_20")
        self.priority = QComboBox()
        self.priority.addItem("高", "high")
        self.priority.addItem("中", "medium")
        self.priority.addItem("低", "low")
        priority_index = self.priority.findData(self.note.get("priority", "medium"))
        self.priority.setCurrentIndex(max(0, priority_index))
        self.characters = QLineEdit(", ".join(self.note.get("related_characters") or []))
        self.characters.setPlaceholderText("多个角色用逗号分隔")
        self.tags = QLineEdit(", ".join(self.note.get("tags") or []))
        self.tags.setPlaceholderText("多个标签用逗号分隔")

        form.addRow("标题", self.title)
        form.addRow("伏笔说明", self.description)
        form.addRow("首次出现章节", self.first_seen)
        form.addRow("计划回收章节", self.planned_resolution)
        form.addRow("优先级", self.priority)
        form.addRow("关联人物", self.characters)
        form.addRow("标签", self.tags)

        if note:
            status = _status_label(str(note.get("status") or "open"))
            recent = str(note.get("recent_seen_chapter") or "尚未记录")
            resolved = str(note.get("resolved_chapter") or "尚未回收")
            form.addRow("当前状态", _readonly_label(status))
            form.addRow("最近出现章节", _readonly_label(recent))
            form.addRow("回收章节", _readonly_label(resolved))

        root.addLayout(form)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def values(self) -> dict:
        return {
            "title": self.title.text().strip(),
            "note": self.description.toPlainText().strip(),
            "first_seen_chapter": self.first_seen.text().strip(),
            "planned_resolution_chapter": self.planned_resolution.text().strip(),
            "priority": self.priority.currentData(),
            "related_characters": _split_list(self.characters.text()),
            "tags": _split_list(self.tags.text()),
        }


def _split_list(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def _status_label(status: str) -> str:
    return {
        "open": "未回收",
        "resolved": "已回收",
        "abandoned": "已放弃",
    }.get(status, status)


def _readonly_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setObjectName("mutedLabel")
    return label
