"""Modal selection of author-owned foreshadowing for one AI expansion."""

from __future__ import annotations

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from core.project import chapter_number_from_id

MAX_SELECTED_FORESHADOWING = 8
NEAR_TERM_CHAPTER_WINDOW = 3


class ForeshadowingSelectionDialog(QDialog):
    """Let the author choose which open notes should enter one expansion."""

    def __init__(
        self,
        notes: list[dict],
        current_chapter_id: str,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.notes = sort_foreshadowing_notes(notes, current_chapter_id)
        self.current_chapter_id = str(current_chapter_id or "")
        self.setWindowTitle("选择本次扩写关注的伏笔")
        self.resize(650, 520)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        hint = QLabel(
            "可选择本次扩写需要重点关注的伏笔。未选择的伏笔不会主动注入本次扩写提示词；"
            f"最多选择 {MAX_SELECTED_FORESHADOWING} 条。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        root.addWidget(hint)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("foreshadowingSelectionList")
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.list_widget.itemChanged.connect(self._on_item_changed)
        root.addWidget(self.list_widget, 1)

        actions = QHBoxLayout()
        recent_button = QPushButton("选择近期计划")
        recent_button.setObjectName("secondaryButton")
        recent_button.clicked.connect(self._select_near_term)
        all_button = QPushButton("全选")
        all_button.setObjectName("secondaryButton")
        all_button.clicked.connect(self._select_all)
        clear_button = QPushButton("全部取消")
        clear_button.setObjectName("ghostButton")
        clear_button.clicked.connect(self._clear_all)
        actions.addWidget(recent_button)
        actions.addWidget(all_button)
        actions.addWidget(clear_button)
        actions.addStretch(1)
        root.addLayout(actions)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认选择")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消扩写")
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate()

    def selected_notes(self) -> list[dict]:
        """Return deep-copied note snapshots selected by the author."""
        selected = []
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.checkState() != Qt.CheckState.Checked:
                continue
            note = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(note, dict):
                selected.append(deepcopy(note))
        return selected

    def _populate(self) -> None:
        self.list_widget.blockSignals(True)
        try:
            self.list_widget.clear()
            for note in self.notes:
                title = str(note.get("title") or "未命名伏笔")
                priority = _priority_label(note.get("priority"))
                planned = str(note.get("planned_resolution_chapter") or "未计划")
                recent = str(note.get("recent_seen_chapter") or "未记录")
                item = QListWidgetItem(f"[{priority}] {title}  ·  计划回收：{planned}")
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                item.setData(Qt.ItemDataRole.UserRole, deepcopy(note))
                item.setToolTip(_note_tooltip(note, recent))
                self.list_widget.addItem(item)
        finally:
            self.list_widget.blockSignals(False)

    def _on_item_changed(self, _item: QListWidgetItem) -> None:
        # The hard limit is validated again on acceptance; this handler keeps
        # the list responsive without silently unchecking the author's choice.
        return

    def _select_near_term(self) -> None:
        current = chapter_number_from_id(self.current_chapter_id)
        self._set_all_checked(False)
        if current is None:
            return
        for index, note in enumerate(self.notes):
            planned = chapter_number_from_id(note.get("planned_resolution_chapter", ""))
            if planned is not None and current <= planned <= current + NEAR_TERM_CHAPTER_WINDOW:
                self.list_widget.item(index).setCheckState(Qt.CheckState.Checked)

    def _select_all(self) -> None:
        if len(self.notes) > MAX_SELECTED_FORESHADOWING:
            QMessageBox.information(
                self,
                "选择数量限制",
                f"当前有 {len(self.notes)} 条未回收伏笔，最多选择 {MAX_SELECTED_FORESHADOWING} 条。"
                "可先使用“选择近期计划”或手动勾选。",
            )
            return
        self._set_all_checked(True)

    def _clear_all(self) -> None:
        self._set_all_checked(False)

    def _set_all_checked(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.list_widget.blockSignals(True)
        try:
            for index in range(self.list_widget.count()):
                self.list_widget.item(index).setCheckState(state)
        finally:
            self.list_widget.blockSignals(False)

    def _accept_selection(self) -> None:
        selected = self.selected_notes()
        if len(selected) > MAX_SELECTED_FORESHADOWING:
            QMessageBox.warning(
                self,
                "选择数量过多",
                f"本次最多选择 {MAX_SELECTED_FORESHADOWING} 条伏笔，请减少选择后继续。",
            )
            return
        self.accept()


def sort_foreshadowing_notes(notes: list[dict], current_chapter_id: str) -> list[dict]:
    """Sort notes by priority, near-term plan, and stable title."""
    current = chapter_number_from_id(current_chapter_id)
    priority_rank = {"high": 0, "medium": 1, "low": 2}

    def key(note: dict) -> tuple[int, int, int, str]:
        planned = chapter_number_from_id(note.get("planned_resolution_chapter", ""))
        if current is None or planned is None:
            plan_group, distance = 1, 999999
        else:
            distance = planned - current
            plan_group = 0 if distance >= 0 else 1
        return (
            priority_rank.get(str(note.get("priority") or "medium"), 1),
            plan_group,
            abs(distance),
            str(note.get("title") or "").casefold(),
        )

    return sorted(
        [deepcopy(note) for note in notes if str(note.get("status") or "open") == "open"],
        key=key,
    )


def _priority_label(priority: object) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(str(priority), "中")


def _note_tooltip(note: dict, recent: str) -> str:
    characters = ", ".join(str(item) for item in note.get("related_characters") or []) or "未指定"
    description = str(note.get("note") or "暂无说明")
    return (
        f"说明：{description}\n"
        f"首次出现：{note.get('first_seen_chapter') or '未记录'}\n"
        f"最近出现：{recent}\n"
        f"关联人物：{characters}"
    )
