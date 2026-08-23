from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.project import NovelProject
from core.project_data import ProjectDataStore
from ui.icons import IconTextButton, material_icon, set_button_icon
from ui.theme import colors_for


class LeftPanel(QWidget):
    """Project-local navigation used by the writing and canon routes."""

    file_selected = Signal(str, str)
    new_chapter_requested = Signal()
    toggle_requested = Signal()

    PATH_ROLE = int(Qt.ItemDataRole.UserRole)
    CATEGORY_ROLE = PATH_ROLE + 1
    ICON_ROLE = PATH_ROLE + 2
    GROUP_ICONS = {
        "总大纲": "account_tree",
        "章节": "auto_stories",
        "角色": "group",
        "世界观": "public",
        "能力体系": "bolt",
        "时间线": "schedule",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("navigationPanel")
        self.setMinimumWidth(230)
        self._project: NovelProject | None = None
        self._scope = "all"
        self._selected_path: str | None = None
        self._icon_color = "#63748A"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 10, 12)
        layout.setSpacing(10)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("项目资料")
        title.setObjectName("panelTitle")
        self.project_label = QLabel("尚未打开项目")
        self.project_label.setObjectName("mutedLabel")
        title_box.addWidget(title)
        title_box.addWidget(self.project_label)
        add_btn = IconTextButton("add", "章节")
        add_btn.setObjectName("smallAccentButton")
        add_btn.setToolTip("新建章节")
        add_btn.clicked.connect(self.new_chapter_requested)
        header_layout.addLayout(title_box, 1)
        header_layout.addWidget(add_btn)
        toggle_btn = QToolButton()
        toggle_btn.setObjectName("panelToggleButton")
        set_button_icon(toggle_btn, "close", size=17)
        toggle_btn.setToolTip("收起资料面板（Ctrl+Shift+L 可恢复）")
        toggle_btn.clicked.connect(self.toggle_requested)
        header_layout.addWidget(toggle_btn)
        layout.addWidget(header)

        self.search = QLineEdit()
        self.search.setObjectName("navigationSearch")
        self.search.setPlaceholderText("搜索章节、角色或设定")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_tree)
        layout.addWidget(self.search)

        self.tree = QTreeWidget()
        self.tree.setObjectName("projectTree")
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(0)
        self.tree.setRootIsDecorated(False)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree, 1)

        hint = QLabel("单击打开 · Ctrl+S 保存 · Ctrl+K 专注")
        hint.setObjectName("navHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

    def set_scope(self, scope: str) -> None:
        self._scope = scope if scope in {"all", "canon"} else "all"
        self.set_project(self._project)

    def set_theme(self, config: dict | None = None) -> None:
        self._icon_color = colors_for(config)["muted_text_color"]
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            group = root.child(index)
            icon_name = group.data(0, self.ICON_ROLE) or "folder"
            group.setIcon(0, material_icon(str(icon_name), self._icon_color, 18))
            for child_index in range(group.childCount()):
                group.child(child_index).setIcon(0, material_icon("description", self._icon_color, 18))

    def set_project(self, project: NovelProject | None) -> None:
        same_project = bool(
            self._project
            and project
            and self._project.root.resolve() == project.root.resolve()
        )
        selected_path = self._selected_path if same_project else None
        self._project = project
        self._selected_path = selected_path
        self.tree.clear()
        self.project_label.setText(project.name if project else "尚未打开项目")
        if project is None:
            return

        store = ProjectDataStore(project)
        groups: list[tuple[str, str, list[Path]]] = [
            ("总大纲", "01", [project.outline_dir / "main_arc.md"]),
            ("章节", "02", store.list_chapters()),
            ("角色", "03", store.list_characters()),
            ("世界观", "04", store.list_world()),
            ("能力体系", "05", store.list_power()),
            ("时间线", "06", [project.canon_dir / "timeline.md"]),
        ]
        if self._scope == "canon":
            groups = [groups[index] for index in (0, 2, 3, 4, 5)]
        category_map = {"能力体系": "战力"}
        for label, number, paths in groups:
            existing = [path for path in paths if path.exists()]
            group = QTreeWidgetItem([f"{number}   {label}   {len(existing)}"])
            group.setData(0, self.CATEGORY_ROLE, category_map.get(label, label))
            group.setData(0, self.ICON_ROLE, self.GROUP_ICONS.get(label, "folder"))
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            group_font = group.font(0)
            group_font.setWeight(QFont.Weight.DemiBold)
            group.setFont(0, group_font)
            group.setIcon(0, material_icon(self.GROUP_ICONS.get(label, "folder"), self._icon_color, 18))
            self.tree.addTopLevelItem(group)
            for path in existing:
                child = QTreeWidgetItem([store.chapter_display_name(path)])
                child.setToolTip(0, str(path))
                child.setData(0, self.PATH_ROLE, str(path))
                child.setData(0, self.CATEGORY_ROLE, category_map.get(label, label))
                child.setIcon(0, material_icon("description", self._icon_color, 18))
                group.addChild(child)
            group.setExpanded(True)
        self._filter_tree(self.search.text())
        self._restore_selection()

    def select_path(self, path: Path) -> None:
        target = str(Path(path))
        self._selected_path = target
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            group = root.child(index)
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                if child.data(0, self.PATH_ROLE) == target:
                    self.tree.setCurrentItem(child)
                    self.tree.scrollToItem(child)
                    self._on_item_clicked(child, 0)
                    return

    def _restore_selection(self) -> None:
        if not self._selected_path:
            return
        target = self._selected_path
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            group = root.child(index)
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                if child.data(0, self.PATH_ROLE) == target and not child.isHidden():
                    self.tree.setCurrentItem(child)
                    self.tree.scrollToItem(child)
                    return

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path_str = item.data(0, self.PATH_ROLE)
        if path_str:
            self._selected_path = str(path_str)
            category = item.data(0, self.CATEGORY_ROLE) or ""
            self.file_selected.emit(category, path_str)

    def _filter_tree(self, query: str) -> None:
        needle = query.strip().casefold()
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            group = root.child(index)
            visible_children = 0
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                visible = not needle or needle in child.text(0).casefold()
                child.setHidden(not visible)
                visible_children += int(visible)
            group.setHidden(bool(needle) and visible_children == 0)
            if needle and visible_children:
                group.setExpanded(True)
        self._restore_selection()
