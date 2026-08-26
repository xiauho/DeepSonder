"""Project recycle-bin dialog for deleted project data."""

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
    QTabWidget,
    QVBoxLayout,
)

from core.foreshadowing import ForeshadowingTrashEntry
from core.project_data import (
    CanonEntryConflictError,
    CanonTrashEntry,
    CharacterIdConflictError,
    CharacterTrashEntry,
    ChapterIdConflictError,
    ProjectDataStore,
    TrashEntry,
)
from ui.icons import set_button_icon


class TrashDialog(QDialog):
    """List, restore, or permanently remove deleted project data."""

    changed = Signal()

    def __init__(self, store: ProjectDataStore, parent=None):
        super().__init__(parent)
        self.store = store
        self.setObjectName("trashDialog")
        self.setWindowTitle("回收站")
        self.resize(680, 470)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        hint = QLabel(
            "章节、角色卡、设定资料和伏笔分别保存在回收站中，恢复后会回到原来的数据位置。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        layout.addWidget(hint)

        self.tabs = QTabWidget()
        self.chapter_list = self._new_list()
        self.character_list = self._new_list()
        self.canon_list = self._new_list()
        self.foreshadowing_list = self._new_list()
        for widget in (
            self.chapter_list,
            self.character_list,
            self.canon_list,
            self.foreshadowing_list,
        ):
            widget.itemSelectionChanged.connect(self._sync_buttons)
            widget.itemDoubleClicked.connect(lambda _item: self.restore_selected())
        # Keep the old attribute available for focused callers and tests.
        self.list_widget = self.chapter_list
        self.tabs.addTab(self.chapter_list, "章节")
        self.tabs.addTab(self.character_list, "角色卡")
        self.tabs.addTab(self.canon_list, "设定资料")
        self.tabs.addTab(self.foreshadowing_list, "伏笔")
        self.tabs.currentChanged.connect(lambda _index: self._sync_buttons())
        layout.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        self.restore_button = QPushButton("恢复")
        self.restore_button.setObjectName("accentButton")
        self.restore_button.clicked.connect(self.restore_selected)
        self.delete_button = QPushButton("永久删除")
        self.delete_button.setObjectName("ghostButton")
        self.delete_button.clicked.connect(self.delete_selected)
        close_button = QPushButton("关闭")
        close_button.setObjectName("ghostButton")
        set_button_icon(close_button, "close", size=16)
        close_button.clicked.connect(self.reject)
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.refresh()

    @staticmethod
    def _new_list() -> QListWidget:
        widget = QListWidget()
        widget.setObjectName("trashList")
        widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        return widget

    def refresh(self) -> None:
        self._refresh_chapters()
        self._refresh_characters()
        self._refresh_canon()
        self._refresh_foreshadowing()
        self._sync_buttons()

    def _refresh_chapters(self) -> None:
        self.chapter_list.clear()
        entries = self.store.list_trash()
        for entry in entries:
            item = QListWidgetItem(self._chapter_label(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry.trash_id)
            item.setToolTip(
                f"原始路径：{entry.original_path}\n删除时间：{entry.deleted_at}"
            )
            self.chapter_list.addItem(item)
        if entries:
            self.chapter_list.setCurrentRow(0)

    def _refresh_foreshadowing(self) -> None:
        self.foreshadowing_list.clear()
        entries = self.store.list_foreshadowing_trash()
        for entry in entries:
            item = QListWidgetItem(self._foreshadowing_label(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry.trash_id)
            item.setToolTip(f"伏笔 ID：{entry.foreshadowing_id}\n删除时间：{entry.deleted_at}")
            self.foreshadowing_list.addItem(item)
        if entries:
            self.foreshadowing_list.setCurrentRow(0)

    def _refresh_canon(self) -> None:
        self.canon_list.clear()
        entries = self.store.list_canon_trash()
        for entry in entries:
            item = QListWidgetItem(self._canon_label(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry.trash_id)
            item.setToolTip(
                f"原始路径：{entry.original_path}\n删除时间：{entry.deleted_at}"
            )
            self.canon_list.addItem(item)
        if entries:
            self.canon_list.setCurrentRow(0)

    def _refresh_characters(self) -> None:
        self.character_list.clear()
        entries = self.store.list_character_trash()
        for entry in entries:
            item = QListWidgetItem(self._character_label(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry.trash_id)
            item.setToolTip(
                f"原始路径：{entry.original_path}\n删除时间：{entry.deleted_at}"
            )
            self.character_list.addItem(item)
        if entries:
            self.character_list.setCurrentRow(0)

    def restore_selected(self) -> None:
        trash_id = self._selected_id()
        if trash_id is None:
            return
        try:
            if self._active_kind() == "chapter":
                self.store.restore_trash_item(trash_id)
            elif self._active_kind() == "character":
                self.store.restore_character_trash_item(trash_id)
            elif self._active_kind() == "canon":
                self.store.restore_canon_trash_item(trash_id)
            else:
                self.store.restore_foreshadowing(trash_id)
        except ChapterIdConflictError as exc:
            answer = QMessageBox.question(
                self,
                "章节 ID 已存在",
                f"原章节 ID“{exc.chapter_id}”已被占用。\n\n"
                f"是否恢复为“{exc.suggested_id}”？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self.store.restore_trash_item(trash_id, conflict_policy="rename")
            except (OSError, ValueError, KeyError) as retry_exc:
                QMessageBox.critical(self, "恢复失败", str(retry_exc))
                return
        except CharacterIdConflictError as exc:
            answer = QMessageBox.question(
                self,
                "角色卡名称已存在",
                f"角色卡“{exc.character_id}”已被占用。\n\n"
                f"是否恢复为“{exc.suggested_id}”？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self.store.restore_character_trash_item(
                    trash_id, conflict_policy="rename"
                )
            except (OSError, ValueError, KeyError) as retry_exc:
                QMessageBox.critical(self, "恢复失败", str(retry_exc))
                return
        except CanonEntryConflictError as exc:
            labels = {"world": "世界观条目", "power": "体系设定", "timeline": "时间线"}
            label = labels.get(exc.kind, "故事资料")
            if not exc.suggested_id:
                QMessageBox.warning(
                    self,
                    "无法恢复",
                    f"{label}的原始位置已经存在内容，请先处理当前文件后再恢复。",
                )
                return
            answer = QMessageBox.question(
                self,
                f"{label}名称已存在",
                f"{label}“{exc.entry_id}”已被占用。\n\n"
                f"是否恢复为“{exc.suggested_id}”？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
            try:
                self.store.restore_canon_trash_item(
                    trash_id, conflict_policy="rename"
                )
            except (OSError, ValueError, KeyError) as retry_exc:
                QMessageBox.critical(self, "恢复失败", str(retry_exc))
                return
        except FileExistsError:
            QMessageBox.warning(
                self,
                "无法恢复",
                "原数据位置已经存在同名内容，请先处理冲突后再恢复。",
            )
            return
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, "恢复失败", str(exc))
            return
        self.changed.emit()
        self.refresh()

    def delete_selected(self) -> None:
        trash_id = self._selected_id()
        if trash_id is None:
            return
        kind_label = {
            "chapter": "章节",
            "character": "角色卡",
            "canon": "设定资料",
            "foreshadowing": "伏笔",
        }[self._active_kind()]
        answer = QMessageBox.question(
            self,
            "永久删除",
            f"该{kind_label}将被永久删除，无法恢复。确定继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            if self._active_kind() == "chapter":
                self.store.delete_trash_item(trash_id)
            elif self._active_kind() == "character":
                self.store.delete_character_trash_item(trash_id)
            elif self._active_kind() == "canon":
                self.store.delete_canon_trash_item(trash_id)
            else:
                self.store.delete_foreshadowing_trash(trash_id)
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.critical(self, "永久删除失败", str(exc))
            return
        self.changed.emit()
        self.refresh()

    def _active_kind(self) -> str:
        if self.tabs.currentWidget() is self.chapter_list:
            return "chapter"
        if self.tabs.currentWidget() is self.character_list:
            return "character"
        if self.tabs.currentWidget() is self.canon_list:
            return "canon"
        return "foreshadowing"

    def _active_list(self) -> QListWidget:
        if self._active_kind() == "chapter":
            return self.chapter_list
        if self._active_kind() == "character":
            return self.character_list
        if self._active_kind() == "canon":
            return self.canon_list
        return self.foreshadowing_list

    def _selected_id(self) -> str | None:
        item = self._active_list().currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def _sync_buttons(self) -> None:
        enabled = self._selected_id() is not None
        self.restore_button.setEnabled(enabled)
        self.delete_button.setEnabled(enabled)

    @staticmethod
    def _chapter_label(entry: TrashEntry) -> str:
        deleted_at = entry.deleted_at.replace("T", " ").replace("+00:00", " UTC")
        return f"{entry.title}  ·  {entry.chapter_id}  ·  {deleted_at}"

    @staticmethod
    def _foreshadowing_label(entry: ForeshadowingTrashEntry) -> str:
        deleted_at = entry.deleted_at.replace("T", " ").replace("+00:00", " UTC")
        return f"{entry.title}  ·  {entry.foreshadowing_id}  ·  {deleted_at}"

    @staticmethod
    def _character_label(entry: CharacterTrashEntry) -> str:
        deleted_at = entry.deleted_at.replace("T", " ").replace("+00:00", " UTC")
        return f"{entry.title}  ·  {entry.character_id}  ·  {deleted_at}"

    @staticmethod
    def _canon_label(entry: CanonTrashEntry) -> str:
        deleted_at = entry.deleted_at.replace("T", " ").replace("+00:00", " UTC")
        labels = {"world": "世界观", "power": "体系设定", "timeline": "时间线"}
        label = labels.get(entry.kind, "故事资料")
        return f"[{label}] {entry.title}  ·  {entry.entry_id}  ·  {deleted_at}"
