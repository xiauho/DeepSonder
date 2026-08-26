from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
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
    new_canon_requested = Signal(str)
    system_importance_requested = Signal(str, str)
    delete_chapter_requested = Signal(str)
    delete_character_requested = Signal(str)
    delete_canon_requested = Signal(str, str)
    new_timeline_requested = Signal()
    toggle_requested = Signal()

    PATH_ROLE = int(Qt.ItemDataRole.UserRole)
    CATEGORY_ROLE = PATH_ROLE + 1
    ICON_ROLE = PATH_ROLE + 2
    IMPORTANCE_ROLE = PATH_ROLE + 3
    GROUP_NUMBER_ROLE = PATH_ROLE + 4
    STATUS_COLUMN_WIDTH = 94
    COMPACT_TREE_WIDTH = 300
    IMPORTANCE_LABELS = {
        "core": "核心",
        "non_core": "非核心",
        "always": "常驻",
    }
    IMPORTANCE_TOOLTIPS = {
        "core": "核心体系：后续 AI 任务会自动作为重点上下文加载。点击可修改。",
        "non_core": "非核心体系：平时作为背景，AI 扩写时可手动选择。点击可修改。",
        "always": "常驻：始终生效，不参与核心/非核心分级。",
    }
    GROUP_ICONS = {
        "总大纲": "account_tree",
        "章节": "auto_stories",
        "角色": "group",
        "世界观": "public",
        "体系设定": "bolt",
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
        self._accent_color = "#2F80ED"
        self._muted_color = "#63748A"
        self._compact_mode = False

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

        canon_add_btn = QToolButton()
        canon_add_btn.setObjectName("smallAccentButton")
        canon_add_btn.setText("新建资料")
        canon_add_btn.setToolTip("新建角色、世界观、体系设定或时间线")
        canon_menu = QMenu(canon_add_btn)
        for kind, label in (
            ("character", "新建角色"),
            ("world", "新建世界观条目"),
            ("power", "新建体系设定"),
            ("timeline", "新建时间线"),
        ):
            action = canon_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, entry_kind=kind: self.new_canon_requested.emit(
                    entry_kind
                )
            )
        canon_add_btn.setMenu(canon_menu)
        canon_add_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        header_layout.addWidget(canon_add_btn)

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
        self.tree.setColumnCount(2)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, self.STATUS_COLUMN_WIDTH)
        self.tree.setIndentation(0)
        self.tree.setRootIsDecorated(False)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        layout.addWidget(self.tree, 1)

        self._nav_hint = QLabel("单击打开 · 点击体系状态可设置加载策略")
        self._nav_hint.setObjectName("navHint")
        self._nav_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._nav_hint)
        self._update_tree_layout()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_tree_layout()

    def _update_tree_layout(self) -> None:
        """Give document titles the full row when the navigation is narrow."""
        if not hasattr(self, "tree"):
            return
        compact = self.tree.width() < self.COMPACT_TREE_WIDTH
        if compact == self._compact_mode:
            return
        self._compact_mode = compact
        self.tree.setColumnHidden(1, compact)
        if not compact:
            self.tree.setColumnWidth(1, self.STATUS_COLUMN_WIDTH)
        if hasattr(self, "_nav_hint"):
            self._nav_hint.setText(
                "单击打开 · 右键体系设定可设置加载策略"
                if compact
                else "单击打开 · 点击体系状态可设置加载策略"
            )

    def set_scope(self, scope: str) -> None:
        self._scope = scope if scope in {"all", "canon"} else "all"
        self.set_project(self._project)

    def set_theme(self, config: dict | None = None) -> None:
        colors = colors_for(config)
        self._icon_color = colors["muted_text_color"]
        self._accent_color = colors["accent_color"]
        self._muted_color = colors["muted_text_color"]
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            group = root.child(index)
            icon_name = group.data(0, self.ICON_ROLE) or "folder"
            group.setIcon(0, material_icon(str(icon_name), self._icon_color, 18))
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                child.setIcon(0, material_icon("description", self._icon_color, 18))
                if child.data(0, self.CATEGORY_ROLE) == "体系设定":
                    self._apply_importance_appearance(child)

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
            (
                "总大纲",
                "01",
                [
                    project.outline_dir / "main_arc.md",
                    project.outline_dir / "future_plan.md",
                ],
            ),
            ("章节", "02", store.list_chapters()),
            ("角色", "03", store.list_characters()),
            ("世界观", "04", store.list_world()),
            ("体系设定", "05", store.list_power()),
            ("时间线", "06", [project.canon_dir / "timeline.md"]),
        ]
        if self._scope == "canon":
            groups = [groups[index] for index in (0, 2, 3, 4, 5)]
        category_map = {"体系设定": "体系设定"}
        for label, number, paths in groups:
            existing = [path for path in paths if path.exists()]
            core_count = (
                len(store.list_core_systems()) if label == "体系设定" else 0
            )
            group_text = f"{number}   {label}   {len(existing)}"
            if label == "体系设定":
                group_text += f" · 核心 {core_count}"
            group = QTreeWidgetItem([group_text, ""])
            group.setData(0, self.CATEGORY_ROLE, category_map.get(label, label))
            group.setData(0, self.ICON_ROLE, self.GROUP_ICONS.get(label, "folder"))
            group.setData(0, self.GROUP_NUMBER_ROLE, number)
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            group.setFirstColumnSpanned(True)
            group_font = group.font(0)
            group_font.setWeight(QFont.Weight.DemiBold)
            group.setFont(0, group_font)
            group.setIcon(0, material_icon(self.GROUP_ICONS.get(label, "folder"), self._icon_color, 18))
            self.tree.addTopLevelItem(group)
            for path in existing:
                display_name = store.chapter_display_name(path)
                status = ""
                importance = ""
                if label == "体系设定":
                    if store.is_core_power_path(path):
                        importance = "always"
                    else:
                        importance = store.system_metadata(path).get("importance")
                    status = self.IMPORTANCE_LABELS.get(importance, "非核心")
                child = QTreeWidgetItem([display_name, status])
                child.setTextAlignment(
                    0,
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                )
                child.setTextAlignment(
                    1,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                )
                child.setToolTip(0, str(path))
                child.setData(0, self.PATH_ROLE, str(path))
                child.setData(0, self.CATEGORY_ROLE, category_map.get(label, label))
                if importance:
                    child.setData(0, self.IMPORTANCE_ROLE, importance)
                child.setIcon(0, material_icon("description", self._icon_color, 18))
                group.addChild(child)
                if importance:
                    self._apply_importance_appearance(child)
            group.setExpanded(True)
        self._filter_tree(self.search.text())
        self._restore_selection()
        self._update_tree_layout()

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

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        path_str = item.data(0, self.PATH_ROLE)
        if path_str:
            self._selected_path = str(path_str)
            category = item.data(0, self.CATEGORY_ROLE) or ""
            if (
                category == "体系设定"
                and column == 1
                and not self._compact_mode
            ):
                if item.data(0, self.IMPORTANCE_ROLE) == "always":
                    return
                position = self.tree.viewport().mapToGlobal(
                    self.tree.visualItemRect(item).bottomRight()
                )
                self._show_importance_menu(item, position)
                return
            self.file_selected.emit(category, path_str)

    def _show_context_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        if item is None:
            return
        path_str = item.data(0, self.PATH_ROLE)
        category = item.data(0, self.CATEGORY_ROLE) or ""
        if not path_str and category in {"角色", "世界观", "体系设定", "时间线"}:
            menu = QMenu(self.tree)
            labels = {
                "角色": "character",
                "世界观": "world",
                "体系设定": "power",
                "时间线": "timeline",
            }
            new_action = menu.addAction(
                "新建时间线" if category == "时间线" else f"新建{category}条目"
            )
            new_action.triggered.connect(
                lambda _checked=False, entry_kind=labels[category]: (
                    self.new_timeline_requested.emit()
                    if entry_kind == "timeline"
                    else self.new_canon_requested.emit(entry_kind)
                )
            )
            menu.exec(self.tree.viewport().mapToGlobal(position))
            return
        if path_str and category == "体系设定":
            self._show_importance_menu(
                item, self.tree.viewport().mapToGlobal(position)
            )
            return
        if not path_str or category not in {"章节", "角色", "世界观", "时间线"}:
            return
        menu = QMenu(self.tree)
        delete_action = menu.addAction("移入回收站")
        if category == "章节":
            delete_action.triggered.connect(
                lambda _checked=False, path=str(path_str): self.delete_chapter_requested.emit(path)
            )
        else:
            if category == "角色":
                delete_action.triggered.connect(
                    lambda _checked=False, path=str(path_str): self.delete_character_requested.emit(path)
                )
            else:
                delete_action.triggered.connect(
                    lambda _checked=False, path=str(path_str), value=category: self.delete_canon_requested.emit(
                        value, path
                    )
                )
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _show_importance_menu(self, item: QTreeWidgetItem, global_position) -> None:
        """Show explicit loading choices instead of silently flipping state."""
        path_str = item.data(0, self.PATH_ROLE)
        if not path_str:
            return
        menu = QMenu(self.tree)
        current = item.data(0, self.IMPORTANCE_ROLE) or "non_core"
        if current == "always":
            fixed = menu.addAction("常驻规则 · 始终加载")
            fixed.setEnabled(False)
            menu.exec(global_position)
            return

        title = menu.addAction("AI 加载策略")
        title.setEnabled(False)
        menu.addSeparator()
        core_action = menu.addAction("核心 · 自动作为重点上下文加载")
        optional_action = menu.addAction("非核心 · 扩写时手动选择")
        for action, importance in (
            (core_action, "core"),
            (optional_action, "non_core"),
        ):
            action.setCheckable(True)
            action.setChecked(current == importance)
            action.triggered.connect(
                lambda _checked=False, path=str(path_str), value=importance: self._emit_system_importance(
                    path, value
                )
            )
        menu.addSeparator()
        delete_action = menu.addAction("移入回收站")
        delete_action.triggered.connect(
            lambda _checked=False, path=str(path_str): self.delete_canon_requested.emit(
                "体系设定", path
            )
        )
        menu.exec(global_position)

    def refresh_system_importance(self) -> None:
        """Refresh only system badges and counts while preserving tree state."""
        if self._project is None:
            return
        store = ProjectDataStore(self._project)
        root = self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            group = root.child(index)
            if group.data(0, self.CATEGORY_ROLE) != "体系设定":
                continue
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                path_str = child.data(0, self.PATH_ROLE)
                if not path_str:
                    continue
                path = Path(path_str)
                importance = (
                    "always"
                    if store.is_core_power_path(path)
                    else store.system_metadata(path).get("importance", "non_core")
                )
                child.setData(0, self.IMPORTANCE_ROLE, importance)
                child.setText(1, self.IMPORTANCE_LABELS.get(importance, "非核心"))
                self._apply_importance_appearance(child)
            self._update_system_group_label(group)

    def _update_system_group_label(self, group: QTreeWidgetItem) -> None:
        core_count = sum(
            group.child(index).data(0, self.IMPORTANCE_ROLE) == "core"
            for index in range(group.childCount())
        )
        number = group.data(0, self.GROUP_NUMBER_ROLE) or "05"
        group.setText(
            0,
            f"{number}   体系设定   {group.childCount()} · 核心 {core_count}",
        )

    def _apply_importance_appearance(self, item: QTreeWidgetItem) -> None:
        importance = item.data(0, self.IMPORTANCE_ROLE) or "non_core"
        is_emphasized = importance in {"core", "always"}
        color = self._accent_color if is_emphasized else self._muted_color
        item.setForeground(1, QBrush(QColor(color)))
        item.setToolTip(1, self.IMPORTANCE_TOOLTIPS.get(importance, ""))
        # Keep status metrics identical to the title column.  A bold status
        # changes the font ascent and makes the right column appear to float
        # above the document title, especially in compact rows.
        font = item.font(0)
        font.setWeight(QFont.Weight.Normal)
        item.setFont(1, font)

    def _emit_system_importance(self, path_str: str, importance: str) -> None:
        """Emit a canonical path so the receiving window can persist reliably."""
        path = Path(path_str)
        if self._project is not None and not path.is_absolute():
            project_path = self._project.root / path
            path = project_path if project_path.exists() else path.resolve()
        self.system_importance_requested.emit(str(path), importance)

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
