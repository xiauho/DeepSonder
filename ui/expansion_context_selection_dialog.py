"""Combined selection of foreshadowing and power systems for one expansion."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyle,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from core.project import chapter_number_from_id
from ui.foreshadowing_selection_dialog import (
    MAX_SELECTED_FORESHADOWING,
    NEAR_TERM_CHAPTER_WINDOW,
    sort_foreshadowing_notes,
)

MAX_SELECTED_POWER = 8


class WholeRowCheckListWidget(QListWidget):
    """A checkable list whose row body toggles without double-toggling boxes.

    Qt already toggles an ``ItemIsUserCheckable`` item when its indicator is
    clicked.  The previous implementation also inverted the state from
    ``itemClicked``, so indicator clicks were applied twice.  This widget only
    performs the extra toggle for clicks outside the native indicator and
    delegates indicator clicks to Qt.
    """

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            position = event.position().toPoint()
            item = self.itemAt(position)
            if item is not None and item.flags() & Qt.ItemFlag.ItemIsEnabled:
                option = QStyleOptionViewItem()
                self.initViewItemOption(option)
                option.rect = self.visualItemRect(item)
                option.index = self.indexFromItem(item)
                indicator = self.style().subElementRect(
                    QStyle.SubElement.SE_ItemViewItemCheckIndicator,
                    option,
                    self,
                )
                # Some platform styles do not expose the item-view indicator
                # rectangle until the delegate paints the row.  The native
                # indicator is still located in the leading part of the row,
                # so keep that area delegated to Qt in that case.
                if indicator.isNull():
                    indicator = QRect(option.rect.left(), option.rect.top(), 32, option.rect.height())
                if not indicator.contains(position):
                    item.setCheckState(
                        Qt.CheckState.Unchecked
                        if item.checkState() == Qt.CheckState.Checked
                        else Qt.CheckState.Checked
                    )
                    event.accept()
                    return
        super().mousePressEvent(event)


class ExpansionContextSelectionDialog(QDialog):
    """Choose the optional high-priority context for one expansion request."""

    def __init__(
        self,
        notes: list[dict],
        power_paths: list[Path],
        current_chapter_id: str,
        core_system_paths: list[Path] | None = None,
        core_power_path: Path | None = None,
        style_guide_path: Path | None = None,
        style_guide_active: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.notes = sort_foreshadowing_notes(notes, current_chapter_id)
        self.power_paths = sorted(
            [Path(path) for path in power_paths], key=lambda path: path.name.casefold()
        )
        self.core_system_paths = sorted(
            [Path(path) for path in (core_system_paths or ())],
            key=lambda path: path.name.casefold(),
        )
        self.current_chapter_id = str(current_chapter_id or "")
        self.core_power_path = Path(core_power_path) if core_power_path else None
        self.style_guide_path = Path(style_guide_path) if style_guide_path else None
        self.style_guide_active = bool(style_guide_active)
        self.setWindowTitle("选择本次扩写重点资料")
        self.resize(820, 560)

        root = QVBoxLayout(self)
        root.setSpacing(10)
        hint = QLabel(
            "常驻核心规则会始终生效。请在下方选择本次重点关注的伏笔和体系设定；"
            "未选择的体系设定仍会作为低优先级背景资料提供给 AI。"
        )
        hint.setWordWrap(True)
        hint.setObjectName("mutedLabel")
        root.addWidget(hint)

        style_label = QLabel(self._style_label())
        style_label.setWordWrap(True)
        style_label.setObjectName("sectionTitle")
        if self.style_guide_path is not None:
            style_label.setToolTip(str(self.style_guide_path))
        root.addWidget(style_label)

        core_label = QLabel(self._core_label())
        core_label.setObjectName("sectionTitle")
        root.addWidget(core_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self._build_foreshadowing_panel())
        splitter.addWidget(self._build_power_panel())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("确认选择")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消扩写")
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        self._populate_notes()
        self._populate_core_systems()
        self._populate_power()

    def selected_notes(self) -> list[dict]:
        selected: list[dict] = []
        for index in range(self.foreshadowing_list.count()):
            item = self.foreshadowing_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                note = item.data(Qt.ItemDataRole.UserRole)
                if isinstance(note, dict):
                    selected.append(deepcopy(note))
        return selected

    def selected_power_paths(self) -> list[str]:
        selected: list[str] = [str(path) for path in self.core_system_paths]
        for index in range(self.power_list.count()):
            item = self.power_list.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                path = item.data(Qt.ItemDataRole.UserRole)
                if path:
                    selected.append(str(path))
        return selected

    def _build_foreshadowing_panel(self) -> QWidget:
        panel = QVBoxLayout()
        title = QLabel(f"伏笔（最多 {MAX_SELECTED_FORESHADOWING} 条）")
        title.setObjectName("sectionTitle")
        panel.addWidget(title)
        self.foreshadowing_list = QListWidget()
        self.foreshadowing_list.setObjectName("foreshadowingSelectionList")
        self.foreshadowing_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        panel.addWidget(self.foreshadowing_list, 1)
        actions = QHBoxLayout()
        recent = QPushButton("选择近期计划")
        recent.setObjectName("secondaryButton")
        recent.clicked.connect(self._select_near_term)
        all_button = QPushButton("全选")
        all_button.setObjectName("secondaryButton")
        all_button.clicked.connect(self._select_all_notes)
        clear = QPushButton("清空")
        clear.setObjectName("ghostButton")
        clear.clicked.connect(lambda: self._set_checked(self.foreshadowing_list, False))
        actions.addWidget(recent)
        actions.addWidget(all_button)
        actions.addWidget(clear)
        panel.addLayout(actions)
        wrapper = QWidget()
        wrapper.setLayout(panel)
        return wrapper

    def _build_power_panel(self) -> QWidget:
        panel = QVBoxLayout()
        title = QLabel("体系设定")
        title.setObjectName("sectionTitle")
        panel.addWidget(title)
        core_title = QLabel("自动纳入的核心体系")
        core_title.setObjectName("mutedLabel")
        panel.addWidget(core_title)
        self.core_system_list = QListWidget()
        self.core_system_list.setObjectName("coreSystemSelectionList")
        self.core_system_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        panel.addWidget(self.core_system_list, 1)
        optional_title = QLabel(f"本次可选的非核心体系（最多 {MAX_SELECTED_POWER} 项）")
        optional_title.setObjectName("mutedLabel")
        panel.addWidget(optional_title)
        self.power_list = WholeRowCheckListWidget()
        self.power_list.setObjectName("powerSelectionList")
        self.power_list.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        panel.addWidget(self.power_list, 2)
        actions = QHBoxLayout()
        all_button = QPushButton("全选体系")
        all_button.setObjectName("secondaryButton")
        all_button.clicked.connect(self._select_all_power)
        clear = QPushButton("清空体系")
        clear.setObjectName("ghostButton")
        clear.clicked.connect(lambda: self._set_checked(self.power_list, False))
        actions.addWidget(all_button)
        actions.addWidget(clear)
        actions.addStretch(1)
        panel.addLayout(actions)
        wrapper = QWidget()
        wrapper.setLayout(panel)
        return wrapper

    def _core_label(self) -> str:
        if self.core_power_path and self.core_power_path.exists():
            return "✓ 常驻核心规则：已加载（始终生效）"
        return "⚠ 常驻核心规则：尚未建立（本次将跳过）"

    def _style_label(self) -> str:
        if self.style_guide_active:
            return "✓ 项目写作风格：已填写，本次扩写将自动应用"
        return "○ 项目写作风格：尚未填写，本次不添加额外风格约束"

    def _populate_notes(self) -> None:
        self.foreshadowing_list.clear()
        for note in self.notes:
            title = str(note.get("title") or "未命名伏笔")
            planned = str(note.get("planned_resolution_chapter") or "未计划")
            item = QListWidgetItem(f"{title}  ·  计划回收：{planned}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setData(Qt.ItemDataRole.UserRole, deepcopy(note))
            self.foreshadowing_list.addItem(item)

    def _populate_power(self) -> None:
        self.power_list.clear()
        for path in self.power_paths:
            item = QListWidgetItem(path.stem)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.power_list.addItem(item)

    def _populate_core_systems(self) -> None:
        self.core_system_list.clear()
        if not self.core_system_paths:
            item = QListWidgetItem("（暂无核心体系）")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            self.core_system_list.addItem(item)
            return
        for path in self.core_system_paths:
            item = QListWidgetItem(path.stem)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            self.core_system_list.addItem(item)

    def _select_near_term(self) -> None:
        current = chapter_number_from_id(self.current_chapter_id)
        self._set_checked(self.foreshadowing_list, False)
        if current is None:
            return
        for index, note in enumerate(self.notes):
            planned = chapter_number_from_id(note.get("planned_resolution_chapter", ""))
            if planned is not None and current <= planned <= current + NEAR_TERM_CHAPTER_WINDOW:
                self.foreshadowing_list.item(index).setCheckState(Qt.CheckState.Checked)

    def _select_all_notes(self) -> None:
        if len(self.notes) > MAX_SELECTED_FORESHADOWING:
            QMessageBox.information(
                self,
                "选择数量限制",
                f"最多选择 {MAX_SELECTED_FORESHADOWING} 条伏笔，请手动勾选或选择近期计划。",
            )
            return
        self._set_checked(self.foreshadowing_list, True)

    def _select_all_power(self) -> None:
        if len(self.power_paths) > MAX_SELECTED_POWER:
            QMessageBox.information(
                self,
                "选择数量限制",
                f"当前有 {len(self.power_paths)} 项非核心体系，最多选择 {MAX_SELECTED_POWER} 项。",
            )
            return
        self._set_checked(self.power_list, True)

    @staticmethod
    def _set_checked(widget: QListWidget, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        widget.blockSignals(True)
        try:
            for index in range(widget.count()):
                widget.item(index).setCheckState(state)
        finally:
            widget.blockSignals(False)

    def _accept_selection(self) -> None:
        if len(self.selected_notes()) > MAX_SELECTED_FORESHADOWING:
            QMessageBox.warning(self, "选择数量过多", "本次最多选择 8 条伏笔。")
            return
        selected_optional = sum(
            self.power_list.item(index).checkState() == Qt.CheckState.Checked
            for index in range(self.power_list.count())
        )
        if selected_optional > MAX_SELECTED_POWER:
            QMessageBox.warning(self, "选择数量过多", "本次最多选择 8 项非核心体系。")
            return
        self.accept()
