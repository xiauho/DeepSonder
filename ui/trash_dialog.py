"""Small project-local recycle-bin dialog for deleted chapters."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.project_data import ProjectDataStore, TrashEntry


class TrashDialog(QDialog):
    """List, restore, or permanently remove deleted chapter entries."""

    changed = Signal()

    def __init__(self, store: ProjectDataStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setObjectName("trashDialog")
        self.setWindowTitle("回收站")
        self.resize(620, 430)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        hint = QLabel("已删除的章节会暂存在这里。恢复时将回到原章节位置。")
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("trashList")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.list_widget.itemSelectionChanged.connect(self._sync_buttons)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self.restore_selected())
        layout.addWidget(self.list_widget, 1)

        buttons = QHBoxLayout()
        self.restore_button = QPushButton("恢复")
        self.restore_button.setObjectName("accentButton")
        self.restore_button.clicked.connect(self.restore_selected)
        self.delete_button = QPushButton("永久删除")
        self.delete_button.setObjectName("ghostButton")
        self.delete_button.clicked.connect(self.delete_selected)
        close_button = QPushButton("关闭")
        close_button.setObjectName("ghostButton")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.refresh()

    def refresh(self) -> None:
        self.list_widget.clear()
        entries = self.store.list_trash()
        for entry in entries:
            item = QListWidgetItem(self._label(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry.trash_id)
            item.setToolTip(
                f"原始路径：{entry.original_path}\n删除时间：{entry.deleted_at}"
            )
            self.list_widget.addItem(item)
        if entries:
            self.list_widget.setCurrentRow(0)
        self._sync_buttons()

    def restore_selected(self) -> None:
        trash_id = self._selected_id()
        if trash_id is None:
            return
        try:
            self.store.restore_trash_item(trash_id)
        except FileExistsError:
            QMessageBox.warning(
                self,
                "无法恢复",
                "原章节文件已经存在，请先处理同名章节后再恢复。",
            )
            return
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "恢复失败", str(exc))
            return
        self.changed.emit()
        self.refresh()

    def delete_selected(self) -> None:
        trash_id = self._selected_id()
        if trash_id is None:
            return
        answer = QMessageBox.question(
            self,
            "永久删除",
            "该章节将被永久删除，无法恢复。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete_trash_item(trash_id)
        except (OSError, ValueError) as exc:
            QMessageBox.critical(self, "永久删除失败", str(exc))
            return
        self.changed.emit()
        self.refresh()

    def _selected_id(self) -> str | None:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _sync_buttons(self) -> None:
        enabled = self._selected_id() is not None
        self.restore_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    @staticmethod
    def _label(entry: TrashEntry) -> str:
        deleted_at = entry.deleted_at.replace("T", " ").replace("+00:00", " UTC")
        return f"{entry.title}  ·  {entry.chapter_id}  ·  {deleted_at}"
