from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QButtonGroup,
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
    QSizePolicy,
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
    sync_character_requested = Signal(str)
    delete_canon_requested = Signal(str, str)
    new_timeline_requested = Signal()
    toggle_requested = Signal()
    locate_current_requested = Signal()
    style_requested = Signal()

    PATH_ROLE = int(Qt.ItemDataRole.UserRole)
    CATEGORY_ROLE = PATH_ROLE + 1
    ICON_ROLE = PATH_ROLE + 2
    IMPORTANCE_ROLE = PATH_ROLE + 3
    GROUP_NUMBER_ROLE = PATH_ROLE + 4
    TITLE_ROLE = PATH_ROLE + 5
    STATUS_COLUMN_WIDTH = 64
    COMPACT_TREE_WIDTH = 300
    IMPORTANCE_LABELS = {
        "core": "核心",
        "non_core": "",
        "always": "常驻",
    }
    IMPORTANCE_TOOLTIPS = {
        "core": "核心体系：后续 AI 任务会自动作为重点上下文加载。点击可修改。",
        "non_core": "非核心体系：平时作为背景，AI 扩写时可手动选择。点击可修改。",
        "always": "常驻：始终生效，不参与核心/非核心分级。",
    }
    GROUP_ICONS = {
        "总大纲": "account_tree",
        "本书文风": "stylus",
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
        self._scope = "chapters"
        self._scope_states: dict[str, dict] = {}
        self._selected_path: str | None = None
        self._current_document: str | None = None
        self._icon_color = "#63748A"
        self._accent_color = "#2F80ED"
        self._muted_color = "#63748A"
        self._compact_mode = False
        self._style_available = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 10, 12)
        layout.setSpacing(10)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        self.project_menu = QMenu(self)
        title = QToolButton()
        title.setText("项目")
        title.setAccessibleName("项目菜单")
        title.setToolTip("新建、打开、导入或导出项目")
        title.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        title.setMenu(self.project_menu)
        title.setObjectName("panelTitle")
        self.project_label = QLabel("尚未打开项目")
        self.project_label.setObjectName("mutedLabel")
        self.project_label.setMinimumWidth(0)
        self.project_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        title_box.addWidget(title)
        title_box.addWidget(self.project_label)
        self.add_chapter_button = add_btn = IconTextButton("add", "章节")
        add_btn.setObjectName("smallAccentButton")
        add_btn.setToolTip("新建章节")
        add_btn.clicked.connect(self.new_chapter_requested)
        header_layout.addLayout(title_box, 1)


        self.add_canon_button = canon_add_btn = QToolButton()
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


        toggle_btn = QToolButton()
        toggle_btn.setObjectName("panelToggleButton")
        set_button_icon(toggle_btn, "close", size=17)
        toggle_btn.setToolTip("收起资料面板（Ctrl+Shift+L 可恢复）")
        toggle_btn.clicked.connect(self.toggle_requested)
        header_layout.addWidget(toggle_btn)
        layout.addWidget(header)
        scope_row = QHBoxLayout()
        scope_row.setSpacing(0)
        self.scope_buttons = {}
        scope_group = QButtonGroup(self)
        for scope, label in (("chapters", "章节"), ("canon", "资料")):
            button = QToolButton()
            button.setText(label)
            button.setAccessibleName(f"浏览{label}")
            button.setObjectName("editorModeButton")
            button.setCheckable(True)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            scope_group.addButton(button)
            button.clicked.connect(lambda _checked=False, value=scope: self.set_scope(value))
            scope_row.addWidget(button, 1)
            self.scope_buttons[scope] = button
        layout.addLayout(scope_row)
        create_row = QHBoxLayout()
        create_row.addWidget(add_btn)
        create_row.addWidget(canon_add_btn)
        create_row.addStretch(1)
        layout.addLayout(create_row)

        self.search = QLineEdit()
        self.search.setObjectName("navigationSearch")
        self.search.setPlaceholderText("搜索章节、角色或设定")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_tree)
        search_row = QHBoxLayout()
        search_row.addWidget(self.search, 1)
        self.locate_button = QToolButton()
        self.locate_button.setText("定位当前")
        self.locate_button.setToolTip("在目录中定位当前文档，并清除搜索条件")
        self.locate_button.setAccessibleName("定位当前文档")
        self.locate_button.setEnabled(False)
        self.locate_button.clicked.connect(self.locate_current_requested)
        search_row.addWidget(self.locate_button)
        layout.addLayout(search_row)

        self.tree = QTreeWidget()
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.setObjectName("projectTree")
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.tree.setColumnWidth(1, self.STATUS_COLUMN_WIDTH)
        self.tree.viewport().installEventFilter(self)
        self.tree.setIndentation(16)
        self.tree.setRootIsDecorated(False)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setIconSize(QSize(18, 18))
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemExpanded.connect(self._update_group_icon)
        self.tree.itemCollapsed.connect(self._update_group_icon)
        self.tree.itemClicked.connect(self._on_item_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.itemActivated.connect(self._on_item_clicked)
        layout.addWidget(self.tree, 1)
        self.empty_label = QLabel()
        self.empty_label.setObjectName("mutedLabel")
        self.empty_label.setWordWrap(True)
        layout.addWidget(self.empty_label)
        self.clear_search_button = QPushButton("清除搜索")
        self.clear_search_button.clicked.connect(self.search.clear)
        self.clear_search_button.hide()
        layout.addWidget(self.clear_search_button)

        self._nav_hint = QLabel("单击打开 · 点击体系状态可设置加载策略")
        self._nav_hint.setObjectName("navHint")
        self._nav_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._nav_hint)
        self.style_footer = QFrame()
        footer_layout = QVBoxLayout(self.style_footer)
        footer_layout.setContentsMargins(0, 6, 0, 0)
        separator = QFrame(); separator.setFrameShape(QFrame.Shape.HLine)
        footer_layout.addWidget(separator)
        self.style_button = IconTextButton("stylus", "本书文风")
        self.style_button.setAccessibleName("本书文风")
        self.style_button.setToolTip("管理分区域文风要求、参考样文与审校例外")
        self.style_button.clicked.connect(self.style_requested)
        self.style_button.setEnabled(False)
        footer_layout.addWidget(self.style_button)
        layout.addWidget(self.style_footer)
        self._update_tree_layout()
        self._apply_scope_controls()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_tree_layout()

    def eventFilter(self, watched, event):
        if hasattr(self, "tree") and watched is self.tree.viewport() and event.type() == QEvent.Type.Resize:
            self._update_tree_layout()
        return super().eventFilter(watched, event)

    def _update_tree_layout(self) -> None:
        """Size hidden-header columns against the actual viewport, including scrollbars."""
        if not hasattr(self, "tree") or getattr(self, "_updating_tree_layout", False):
            return
        self._updating_tree_layout = True
        try:
            width = self.tree.viewport().width()
            compact = width < self.COMPACT_TREE_WIDTH
            changed = compact != self._compact_mode
            self._compact_mode = compact
            self.tree.setColumnHidden(1, compact)
            status_width = max(self.STATUS_COLUMN_WIDTH, self.tree.fontMetrics().horizontalAdvance("常驻") + 24)
            self.tree.setColumnWidth(1, status_width)
            self.tree.setColumnWidth(0, max(20, width - (0 if compact else status_width)))
            if changed:
                for item in self.document_items():
                    if item.data(0, self.IMPORTANCE_ROLE):
                        self._apply_importance_appearance(item)
        finally:
            self._updating_tree_layout = False

    def _remember_scope(self) -> None:
        self._scope_states[self._scope] = {
            "query": self.search.text(),
            "selected": self._selected_path,
            "scroll": self.tree.verticalScrollBar().value(),
            "expanded": {self.tree.topLevelItem(i).data(0, self.CATEGORY_ROLE):
                         self.tree.topLevelItem(i).isExpanded()
                         for i in range(self.tree.topLevelItemCount())},
        }

    def _apply_scope_controls(self) -> None:
        chapters = self._scope == "chapters"
        self.scope_buttons[self._scope].setChecked(True)
        self.add_chapter_button.setVisible(chapters)
        self.add_canon_button.setVisible(not chapters)
        self.search.setPlaceholderText("搜索章节…" if chapters else "搜索资料…")
        self.search.setAccessibleName(self.search.placeholderText())
        self._nav_hint.setText("单击打开 · 右键管理章节" if chapters else "单击打开 · 右键管理资料与加载策略")
        self._nav_hint.setWordWrap(True)
        self.style_footer.setVisible(not chapters)
        self._update_locate_button()

    def document_items(self):
        """Yield every document, including the flat planning/timeline rows."""
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            if item.data(0, self.PATH_ROLE):
                yield item
            for child_index in range(item.childCount()):
                child = item.child(child_index)
                if child.data(0, self.PATH_ROLE):
                    yield child

    def set_style_available(self, available: bool):
        self._style_available = available
        self.style_button.setEnabled(self._project is not None and available)

    def set_current_document(self, path):
        self._current_document = str(path) if path else None
        self._update_locate_button()

    def _update_locate_button(self):
        item = next((item for item in self.document_items()
                     if item.data(0, self.PATH_ROLE) == self._current_document), None)
        in_scope = item is not None and ((item.data(0, self.CATEGORY_ROLE) == "章节") == (self._scope == "chapters"))
        self.locate_button.setEnabled(in_scope)

    def set_scope(self, scope: str) -> None:
        scope = "canon" if scope == "canon" else "chapters"
        if scope == self._scope:
            return
        self._remember_scope()
        self._scope = scope
        state = self._scope_states.get(scope, {})
        self._selected_path = state.get("selected")
        self.search.blockSignals(True)
        self.search.setText(state.get("query", ""))
        self.search.blockSignals(False)
        for i in range(self.tree.topLevelItemCount()):
            group = self.tree.topLevelItem(i)
            group.setExpanded(state.get("expanded", {}).get(group.data(0, self.CATEGORY_ROLE), True))
        self.tree.clearSelection()
        self._apply_scope_controls()
        self._filter_tree(self.search.text())
        self.tree.doItemsLayout()
        self.tree.verticalScrollBar().setValue(state.get("scroll", 0))

    def _reveal_scope_for_path(self, path: Path) -> None:
        if self._project is None:
            return
        scope = "chapters" if path.parent.resolve() == self._project.chapters_dir.resolve() else "canon"
        self.set_scope(scope)
        # An explicit navigation request must not be concealed by a search.
        self.search.clear()

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
            if not group.data(0, self.PATH_ROLE): self._update_group_icon(group)
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
        if same_project:
            self._remember_scope()
        else:
            self._scope_states.clear()
            self.search.clear()
        selected_path = self._selected_path if same_project else None
        self._project = project
        self._selected_path = selected_path
        self.tree.clear()
        self.project_label.setText(project.name if project else "尚未打开项目")
        self.project_label.setToolTip(project.name if project else "尚未打开项目")
        self.style_button.setEnabled(project is not None and self._style_available)
        if project is None:
            self._apply_scope_controls()
            self._filter_tree(self.search.text())
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
            ("时间线", "02", [project.canon_dir / "timeline.md"]),
            ("章节", "03", store.list_chapters()),
            ("角色", "04", store.list_characters()),
            ("世界观", "05", store.list_world()),
            ("体系设定", "06", store.list_power()),

        ]
        category_map = {"体系设定": "体系设定"}
        for label, number, paths in groups:
            existing = [path for path in paths if path.exists()]
            flat = label in {"总大纲", "时间线"}
            group_text = f"{label} · {len(existing)}"
            group = QTreeWidgetItem([group_text, ""])
            group.setData(0, self.CATEGORY_ROLE, category_map.get(label, label))
            group.setData(0, self.ICON_ROLE, self.GROUP_ICONS.get(label, "folder"))
            group.setData(0, self.GROUP_NUMBER_ROLE, number)
            group.setFirstColumnSpanned(True)
            group_font = group.font(0)
            group_font.setWeight(QFont.Weight.DemiBold)
            group.setFont(0, group_font)
            group.setIcon(0, material_icon(self.GROUP_ICONS.get(label, "folder"), self._icon_color, 18))
            if not flat:
                self.tree.addTopLevelItem(group)
                group.setFirstColumnSpanned(True)
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
                child.setToolTip(0, f"{display_name}\n{path}")
                child.setData(0, self.PATH_ROLE, str(path))
                child.setData(0, self.TITLE_ROLE, display_name)
                child.setData(0, self.CATEGORY_ROLE, category_map.get(label, label))
                if importance:
                    child.setData(0, self.IMPORTANCE_ROLE, importance)
                icon_name = self.GROUP_ICONS[label] if flat else "description"
                child.setData(0, self.ICON_ROLE, icon_name)
                child.setIcon(0, material_icon(icon_name, self._icon_color, 18))
                if flat:
                    self.tree.addTopLevelItem(child)
                    child.setFirstColumnSpanned(True)
                else:
                    group.addChild(child)
                if importance:
                    self._apply_importance_appearance(child)
            expanded = self._scope_states.get(self._scope, {}).get("expanded", {})
            group.setExpanded(expanded.get(label, True))
            if not flat: self._update_group_icon(group)
        self._filter_tree(self.search.text())
        self._restore_selection()
        self._update_tree_layout()
        self._apply_scope_controls()
        self.tree.doItemsLayout()
        self.tree.verticalScrollBar().setValue(self._scope_states.get(self._scope, {}).get("scroll", 0))

    def select_path(self, path: Path) -> None:
        self._reveal_scope_for_path(Path(path))
        target = str(Path(path))
        self._selected_path = target
        for child in self.document_items():
            if child.data(0, self.PATH_ROLE) == target:
                if child.parent(): child.parent().setExpanded(True)
                self.tree.setCurrentItem(child)
                self.tree.scrollToItem(child)
                self._on_item_clicked(child, 0)
                return

    def reveal_path(self, path: Path | str) -> bool:
        """Select and scroll to a file without emitting a new open request."""
        self._reveal_scope_for_path(Path(path))
        target = str(Path(path))
        self._selected_path = target
        for child in self.document_items():
            if child.data(0, self.PATH_ROLE) == target:
                if child.parent(): child.parent().setExpanded(True)
                self.tree.setCurrentItem(child)
                self.tree.scrollToItem(child)
                return True
        return False

    def _restore_selection(self) -> None:
        if not self._selected_path:
            return
        for child in self.document_items():
            if child.data(0, self.PATH_ROLE) == self._selected_path and not child.isHidden():
                if child.parent():
                    if child.parent().isHidden() or not child.parent().isExpanded(): continue
                self.tree.setCurrentItem(child)
                self.tree.scrollToItem(child)
                return

    def _update_group_icon(self, item):
        if item.data(0, self.PATH_ROLE):
            return
        icon = ("expand_more" if item.isExpanded() else "chevron_right") if item.childCount() else item.data(0, self.ICON_ROLE)
        item.setIcon(0, material_icon(icon or "folder", self._icon_color, 18))
        item.setToolTip(0, "点击展开或收起" if item.childCount() else "暂无条目，可从上方新建资料")

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        path_str = item.data(0, self.PATH_ROLE)
        if not path_str:
            item.setExpanded(not item.isExpanded())
            return
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
        if category == "角色":
            sync_action = menu.addAction("同步角色档案…")
            sync_action.triggered.connect(
                lambda _checked=False, path=str(path_str): self.sync_character_requested.emit(path)
            )
            menu.addSeparator()
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
        group.setText(0, f"体系设定 · {group.childCount()}")

    def _apply_importance_appearance(self, item: QTreeWidgetItem) -> None:
        importance = item.data(0, self.IMPORTANCE_ROLE) or "non_core"
        title = item.data(0, self.TITLE_ROLE) or item.text(0)
        status = self.IMPORTANCE_LABELS.get(importance, "")
        item.setText(0, f"{title} · {status}" if self._compact_mode and status else title)
        item.setText(1, status)
        item.setToolTip(0, f"{title}\n{item.data(0, self.PATH_ROLE)}\n{self.IMPORTANCE_TOOLTIPS.get(importance, '')}")
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
        total_visible = 0
        for index in range(root.childCount()):
            group = root.child(index)
            in_scope = (group.data(0, self.CATEGORY_ROLE) == "章节") == (self._scope == "chapters")
            if group.data(0, self.PATH_ROLE):
                visible = in_scope and (not needle or needle in group.text(0).casefold())
                group.setHidden(not visible)
                total_visible += int(visible)
                continue
            visible_children = 0
            for child_index in range(group.childCount()):
                child = group.child(child_index)
                visible = not needle or needle in child.text(0).casefold()
                child.setHidden(not visible)
                visible_children += int(visible)
            group.setHidden(not in_scope or (bool(needle) and visible_children == 0))
            if in_scope:
                total_visible += visible_children
            if in_scope and needle and visible_children:
                group.setExpanded(True)
        self._restore_selection()
        self.empty_label.setText("没有匹配项，试试其他关键词。" if needle else "暂无章节，点击上方新建章节。" if self._scope == "chapters" else "暂无资料，点击上方新建资料。")
        self.empty_label.setVisible(total_visible == 0)
        self.clear_search_button.setVisible(total_visible == 0 and bool(needle))
