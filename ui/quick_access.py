"""Keyboard-first document and command discovery using the current UI inventory."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup, QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QVBoxLayout,
)


@dataclass(frozen=True)
class QuickEntry:
    key: str
    title: str
    detail: str
    mode: str = "documents"
    shortcut: str = ""
    category: str = ""
    project_root: str = ""
    feature: str = ""


def match_entries(entries, query):
    """Literal, case-insensitive token matching; preserve recent order on ties."""
    words = query.casefold().split()
    matches = [entry for entry in entries if all(
        word in f"{entry.title} {entry.detail} {entry.shortcut}".casefold() for word in words
    )]
    if words:
        matches.sort(key=lambda entry: (not entry.title.casefold().startswith(words[0]),
                                       not all(word in entry.title.casefold() for word in words)))
    return matches


class QuickAccessDialog(QDialog):
    MAX_VISIBLE = 80

    def __init__(self, entries, unavailable: Callable[[QuickEntry], str], *, mode="documents", parent=None):
        super().__init__(parent)
        self.entries = list(entries)
        self.unavailable = unavailable
        self.selected_entry = None
        self.setWindowTitle("快速打开与命令")
        self.setObjectName("quickAccessDialog")
        self.resize(680, 500)
        if parent is not None and parent.screen() is not None:
            area = parent.screen().availableGeometry()
            self.resize(min(680, area.width() - 40), min(500, area.height() - 80))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        tabs = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.mode_buttons = {}
        for key, label in (("documents", "文档  Ctrl+P"), ("commands", "命令  Ctrl+Shift+P")):
            button = QPushButton(label)
            button.setObjectName("quickAccessMode")
            button.setCheckable(True)
            button.setAutoDefault(False)
            button.clicked.connect(lambda _checked=False, target=key: self.set_mode(target))
            self.mode_group.addButton(button)
            self.mode_buttons[key] = button
            tabs.addWidget(button)
        tabs.addStretch()
        close = QPushButton("关闭  Esc")
        close.setAutoDefault(False)
        close.clicked.connect(self.reject)
        tabs.addWidget(close)
        layout.addLayout(tabs)
        self.search_label = QLabel()
        layout.addWidget(self.search_label)
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search_label.setBuddy(self.search)
        self.search.textChanged.connect(self.refresh_results)
        self.search.installEventFilter(self)
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.setObjectName("quickAccessResults")
        self.results.setAccessibleName("搜索结果，使用上下方向键选择，回车打开")
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.installEventFilter(self)
        self.results.itemActivated.connect(self.activate_current)
        self.results.currentItemChanged.connect(self.update_detail)
        layout.addWidget(self.results, 1)
        self.count_label = QLabel()
        self.count_label.setObjectName("mutedLabel")
        layout.addWidget(self.count_label)
        self.detail = QLabel()
        self.detail.setObjectName("quickAccessDetail")
        self.detail.setTextFormat(Qt.TextFormat.PlainText)
        self.detail.setWordWrap(True)
        self.detail.setMinimumHeight(44)
        layout.addWidget(self.detail)
        hint = QLabel("↑ ↓ 选择    双击 / Enter 打开或执行    Esc 关闭")
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)
        self.document_shortcut = QShortcut(QKeySequence("Ctrl+P"), self)
        self.document_shortcut.activated.connect(lambda: self.set_mode("documents"))
        self.command_shortcut = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        self.command_shortcut.activated.connect(lambda: self.set_mode("commands"))
        self.mode = mode
        self.set_mode(mode)

    def set_mode(self, mode):
        self.mode = mode
        self.mode_buttons[mode].setChecked(True)
        documents = mode == "documents"
        self.search_label.setText("查找章节与资料" if documents else "查找常用操作与快捷键")
        self.search.setAccessibleName(self.search_label.text())
        self.search.setPlaceholderText("输入标题、文件名或类别；留空显示最近访问" if documents else "例如：保存、专注、AI 续写、设置")
        self.search.clear()
        self.refresh_results()
        self.search.setFocus()

    def refresh_results(self, *_args):
        self.results.clear()
        matches = match_entries([entry for entry in self.entries if entry.mode == self.mode], self.search.text())
        for entry in matches[:self.MAX_VISIBLE]:
            reason = self.unavailable(entry)
            title = entry.title + (f"    {entry.shortcut}" if entry.shortcut else "")
            subtitle = "不可用 · " + reason if reason else entry.detail
            item = QListWidgetItem(f"{title}\n{subtitle}")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(f"{entry.title}\n{entry.detail}" + (f"\n{reason}" if reason else ""))
            self.results.addItem(item)
        total = len(matches)
        self.count_label.setText(f"{total} 项结果" if total <= self.MAX_VISIBLE else f"找到 {total} 项，显示前 {self.MAX_VISIBLE} 项；输入更多关键词缩小范围")
        if total:
            self.results.setCurrentRow(0)
        else:
            empty_mode = not any(entry.mode == self.mode for entry in self.entries)
            self.detail.setText("尚未打开项目。切换到命令可打开或新建项目。" if empty_mode and self.mode == "documents" else "没有匹配项。请缩短关键词，或清除搜索重试。")

    def update_detail(self, *_args):
        item = self.results.currentItem()
        if item is not None:
            entry = item.data(Qt.ItemDataRole.UserRole)
            self.detail.setText(self.unavailable(entry) or entry.detail)

    def activate_current(self, *_args):
        item = self.results.currentItem()
        if item is None:
            return
        entry = item.data(Qt.ItemDataRole.UserRole)
        reason = self.unavailable(entry)
        if reason:
            self.detail.setText("暂时无法执行：" + reason)
            return
        self.selected_entry = entry
        self.accept()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.activate_current()
                return True
            if obj is self.search and key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                if self.results.count():
                    row = self.results.currentRow() + (1 if key == Qt.Key.Key_Down else -1)
                    self.results.setCurrentRow(max(0, min(self.results.count() - 1, row)))
                return True
        return super().eventFilter(obj, event)
