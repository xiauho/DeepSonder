from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QEvent, QModelIndex, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QToolButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.canon_schema import load_canon_entry, entry_status
from core.character_identity import load_registry
from core.project import NovelProject
from core.project_data import ProjectDataStore
from ui.icons import IconTextButton, material_icon, set_button_icon
from ui.theme import colors_for


class StatusDotDelegate(QStyledItemDelegate):
    """状态列只画圆点，底色固定为资料栏背景，不随整行选中或悬停变色。"""

    def paint(self, painter, option, index):
        if index.column() != 1:
            super().paint(painter, option, index)
            return
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # 名称列仍是完整的状态卡片；状态列若跟随行状态，会变成第二块圆角卡片。
        opt.state &= ~(
            QStyle.StateFlag.State_Selected
            | QStyle.StateFlag.State_MouseOver
            | QStyle.StateFlag.State_Sunken
            | QStyle.StateFlag.State_HasFocus
        )
        # 样式表会按行状态先铺一层底色且不读取 backgroundBrush，必须直接覆盖。
        painter.fillRect(opt.rect, opt.palette.color(QPalette.ColorRole.Window))
        opt.backgroundBrush = QBrush(Qt.BrushStyle.NoBrush)
        if opt.text != "●":
            super().paint(painter, opt, index)
            return
        # Font glyph baselines do not necessarily share the cell's center.
        # Draw both circles around one floating-point center at every DPI.
        center = QRectF(opt.rect).center()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        group = (QPalette.ColorGroup.Active if opt.state & QStyle.StateFlag.State_Enabled
                 else QPalette.ColorGroup.Disabled)
        painter.setBrush(opt.palette.color(group, QPalette.ColorRole.Text))
        radius = max(4.0, min(8.0, opt.fontMetrics.height() * 0.32)) / 2
        painter.drawEllipse(center, radius, radius)
        tree = self.parent()
        current = tree.currentIndex()
        focused = (tree.hasFocus() and current.isValid() and current.row() == index.row()
                   and current.parent() == index.parent())
        hovered = getattr(tree, "_status_hover", QModelIndex()) == index
        if focused or hovered:
            pen = QPen(QColor(tree.property("statusAccentColor")))
            pen.setWidthF(1.5)
            if focused:
                pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            diameter = min(18, opt.rect.width() - 12, opt.rect.height() - 8)
            painter.drawEllipse(QRectF(center.x() - diameter / 2,
                                       center.y() - diameter / 2, diameter, diameter))
        painter.restore()


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
    timeline_requested = Signal()

    PATH_ROLE = int(Qt.ItemDataRole.UserRole)
    CATEGORY_ROLE = PATH_ROLE + 1
    ICON_ROLE = PATH_ROLE + 2
    IMPORTANCE_ROLE = PATH_ROLE + 3
    GROUP_NUMBER_ROLE = PATH_ROLE + 4
    TITLE_ROLE = PATH_ROLE + 5
    FEATURE_ROLE = PATH_ROLE + 6
    CANON_STATUS_ROLE = PATH_ROLE + 7
    STATUS_COLUMN_WIDTH = 64
    COMPACT_TREE_WIDTH = 300
    COMPACT_STATUS_WIDTH = 28
    STATUS_DOT = "●"
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
        "故事规划": "account_tree",
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
        self._selected_feature = None
        self._selected_path: str | None = None
        self._current_document: str | None = None
        self._icon_color = "#63748A"
        self._accent_color = "#2F80ED"
        self._muted_color = "#63748A"
        self._compact_mode = False
        self._style_available = True
        self._canon_status_cache = {}
        self._alias_map = {}
        self._primary_names = set()

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
            ("timeline", "新建事件"),
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
        self.tree._status_hover = QModelIndex()
        self.tree.setProperty("statusAccentColor", self._accent_color)
        self.tree.setMouseTracking(True)
        self.tree.viewport().setMouseTracking(True)
        self.tree.setItemDelegateForColumn(1, StatusDotDelegate(self.tree))
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        # 默认最小节宽会随字号抬高，会挤掉窄侧栏的圆点列，这里明确放开到圆点宽度。
        self.tree.header().setMinimumSectionSize(self.COMPACT_STATUS_WIDTH)
        self.tree.setColumnWidth(1, self.STATUS_COLUMN_WIDTH)
        self.tree.viewport().installEventFilter(self)
        self.tree.installEventFilter(self)
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

        self._nav_hint = QLabel("单击打开 · 点击状态列或右键查看资料状态")
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
        if hasattr(self, "tree"):
            if watched is self.tree.viewport():
                if event.type() in (QEvent.Type.MouseMove, QEvent.Type.HoverMove):
                    index = self.tree.indexAt(event.position().toPoint())
                    item = self.tree.itemFromIndex(index) if index.isValid() else None
                    active = bool(index.column() == 1 and item is not None and (
                        item.data(0, self.CANON_STATUS_ROLE) or item.data(0, self.IMPORTANCE_ROLE)))
                    self.tree._status_hover = index if active else QModelIndex()
                    self.tree.viewport().setCursor(Qt.CursorShape.PointingHandCursor if active
                                                   else Qt.CursorShape.ArrowCursor)
                    self.tree.viewport().update()
                elif event.type() in (QEvent.Type.Leave, QEvent.Type.HoverLeave):
                    self.tree._status_hover = QModelIndex()
                    self.tree.viewport().unsetCursor()
                    self.tree.viewport().update()
            if watched is self.tree:
                if (event.type() == QEvent.Type.KeyPress
                        and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                        and event.modifiers() & Qt.KeyboardModifier.AltModifier):
                    item = self.tree.currentItem()
                    if item is not None and (item.data(0, self.CANON_STATUS_ROLE)
                                             or item.data(0, self.IMPORTANCE_ROLE)):
                        position = self.tree.viewport().mapToGlobal(self.tree.visualItemRect(item).bottomRight())
                        self._show_status_menu(item, position)
                        return True
                if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                    self.tree.viewport().update()
        if hasattr(self, "tree") and (
            (watched is self.tree.viewport() and event.type() == QEvent.Type.Resize)
            or (watched is self.tree and event.type() in {QEvent.Type.FontChange, QEvent.Type.StyleChange})
        ):
            self._update_tree_layout()
        return super().eventFilter(watched, event)

    def _update_tree_layout(self) -> None:
        """Size hidden-header columns against the actual viewport, including scrollbars."""
        if not hasattr(self, "tree") or getattr(self, "_updating_tree_layout", False):
            return
        self._updating_tree_layout = True
        try:
            self.tree.ensurePolished()
            width = self.tree.viewport().width()
            compact = width < self.COMPACT_TREE_WIDTH
            changed = compact != self._compact_mode
            self._compact_mode = compact
            # 窄侧栏保留状态列作为统一状态入口，只是压缩成圆点宽度；点它仍打开资料状态浮层。
            status_width = (
                self.COMPACT_STATUS_WIDTH if compact
                else max(self.STATUS_COLUMN_WIDTH, self.tree.fontMetrics().horizontalAdvance("读取失败") + 36)
            )
            self.tree.setColumnHidden(1, False)
            self.tree.setColumnWidth(1, status_width)
            # 以实际生效的状态列宽反推名称列，避免两列之和超出视口而产生横向滚动。
            self.tree.setColumnWidth(0, max(20, width - self.tree.columnWidth(1)))
            if changed:
                for item in self.document_items():
                    if item.data(0, self.IMPORTANCE_ROLE) or item.data(0, self.CANON_STATUS_ROLE):
                        self._apply_document_appearance(item)
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
        self._nav_hint.setText("单击打开 · 右键管理章节" if chapters else "资料状态：点击圆点、右键或 Alt+Enter")
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
        self.tree.setProperty("statusAccentColor", self._accent_color)
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
                if child.data(0, self.IMPORTANCE_ROLE) or child.data(0, self.CANON_STATUS_ROLE):
                    self._apply_document_appearance(child)

    def set_project(self, project: NovelProject | None) -> None:
        self.tree._status_hover = QModelIndex()
        self.tree.viewport().unsetCursor()
        same_project = bool(
            self._project
            and project
            and self._project.root.resolve() == project.root.resolve()
        )
        if same_project:
            self._remember_scope()
        else:
            self._selected_feature = None
            self._scope_states.clear()
            self._canon_status_cache.clear()
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

        try:
            registry = load_registry(project)
            self._alias_map = {name: group["primary"] for group in registry["groups"]
                               for name in group["names"] if name != group["primary"]}
            self._primary_names = {group["primary"] for group in registry["groups"]}
        except (ValueError, OSError):
            self._alias_map, self._primary_names = {}, set()
        store = ProjectDataStore(project)
        groups: list[tuple[str, str, list[Path]]] = [
            (
                "故事规划",
                "01",
                [
                    project.outline_dir / "story_plan.json",
                ],
            ),
            ("时间线", "02", []),
            ("章节", "03", store.list_chapters()),
            ("角色", "04", store.list_characters()),
            ("世界观", "05", store.list_world()),
            ("体系设定", "06", store.list_power()),

        ]
        category_map = {"体系设定": "体系设定"}
        for label, number, paths in groups:
            if label == "时间线":
                item = QTreeWidgetItem(["时间线", ""])
                item.setData(0, self.CATEGORY_ROLE, "时间线")
                item.setData(0, self.FEATURE_ROLE, "timeline")
                item.setData(0, self.ICON_ROLE, "schedule")
                item.setIcon(0, material_icon("schedule", self._icon_color, 18))
                item.setToolTip(0, "查看全部故事事件与作者编排的时间轴")
                self.tree.addTopLevelItem(item)
                item.setFirstColumnSpanned(True)
                continue
            existing = [path for path in paths if path.exists()]
            flat = label in {"故事规划", "时间线"}
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
                kind = {"角色": "character", "世界观": "world", "体系设定": "power"}.get(label)
                if kind:
                    child.setData(0, self.CANON_STATUS_ROLE, self._canon_status(path, kind))
                    self._apply_document_appearance(child)
                elif importance:
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
        self._selected_feature = None
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
        self._selected_feature = None
        self._selected_path = target
        for child in self.document_items():
            if child.data(0, self.PATH_ROLE) == target:
                if child.parent(): child.parent().setExpanded(True)
                self.tree.setCurrentItem(child)
                self.tree.scrollToItem(child)
                return True
        return False

    def select_timeline(self):
        self.set_scope("canon")
        self.search.clear()
        self._selected_feature = "timeline"
        self._selected_path = None
        self._restore_selection()

    def _restore_selection(self) -> None:
        if self._selected_feature == "timeline":
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                if item.data(0, self.FEATURE_ROLE) == "timeline" and not item.isHidden():
                    self.tree.setCurrentItem(item)
                    return
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
        if item.data(0, self.PATH_ROLE) or item.data(0, self.FEATURE_ROLE):
            return
        icon = ("expand_more" if item.isExpanded() else "chevron_right") if item.childCount() else item.data(0, self.ICON_ROLE)
        item.setIcon(0, material_icon(icon or "folder", self._icon_color, 18))
        item.setToolTip(0, "点击展开或收起" if item.childCount() else "暂无条目，可从上方新建资料")

    def _on_item_clicked(self, item: QTreeWidgetItem, column: int) -> None:
        if item.data(0, self.FEATURE_ROLE) == "timeline":
            self._selected_feature = "timeline"
            self._selected_path = None
            self.timeline_requested.emit()
            return
        self._selected_feature = None
        path_str = item.data(0, self.PATH_ROLE)
        if not path_str:
            item.setExpanded(not item.isExpanded())
            return
        if path_str:
            self._selected_path = str(path_str)
            category = item.data(0, self.CATEGORY_ROLE) or ""
            if (
                column == 1
                and (item.data(0, self.CANON_STATUS_ROLE) or item.data(0, self.IMPORTANCE_ROLE))
            ):
                # 状态列是统一的资料状态入口，宽侧栏显示文字、窄侧栏显示圆点，行为一致。
                position = self.tree.viewport().mapToGlobal(
                    self.tree.visualItemRect(item).bottomRight()
                )
                self._show_status_menu(item, position)
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
                "新建事件" if category == "时间线" else f"新建{category}条目"
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
        if not path_str:
            return
        if item.data(0, self.CANON_STATUS_ROLE) or item.data(0, self.IMPORTANCE_ROLE):
            # 与左键点击状态列一致：资料行只保留一个统一的状态浮层。
            self._show_status_menu(item, self.tree.viewport().mapToGlobal(position))
            return
        if category != "章节":
            return
        menu = QMenu(self.tree)
        delete_action = menu.addAction("移入回收站")
        delete_action.triggered.connect(
            lambda _checked=False, path=str(path_str): self.delete_chapter_requested.emit(path)
        )
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _show_status_menu(self, item: QTreeWidgetItem, global_position) -> None:
        """统一的资料浮层：状态明细 + 角色同步 / 体系加载策略 + 移入回收站。

        左键点击状态列与右键菜单都走这里，行为不再随资料分类变化。
        """
        path_str = item.data(0, self.PATH_ROLE)
        if not path_str:
            return
        path = str(path_str)
        category = item.data(0, self.CATEGORY_ROLE) or ""
        status = item.data(0, self.CANON_STATUS_ROLE) or {}
        importance = item.data(0, self.IMPORTANCE_ROLE)
        menu = QMenu(self.tree)
        title = menu.addAction(item.data(0, self.TITLE_ROLE) or item.text(0))
        title.setEnabled(False)
        short, details = self._status_text(status)
        for line in filter(None, (details or short).split("\n")):
            action = menu.addAction(line)
            action.setEnabled(False)
        if category == "体系设定":
            menu.addSeparator()
            headline = menu.addAction("AI 加载策略")
            headline.setEnabled(False)
            if importance == "always":
                fixed = menu.addAction("常驻规则 · 始终加载")
                fixed.setEnabled(False)
            else:
                for label, value in (
                    ("核心 · 自动作为重点上下文加载", "core"),
                    ("非核心 · 扩写时手动选择", "non_core"),
                ):
                    action = menu.addAction(label)
                    action.setCheckable(True)
                    action.setChecked((importance or "non_core") == value)
                    action.triggered.connect(
                        lambda _checked=False, target=path, choice=value:
                        self._emit_system_importance(target, choice)
                    )
        menu.addSeparator()
        if category == "角色":
            sync_action = menu.addAction("同步角色档案…")
            sync_action.triggered.connect(
                lambda _checked=False, target=path:
                self.sync_character_requested.emit(target)
            )
            menu.addSeparator()
        delete_action = menu.addAction("移入回收站")
        delete_action.triggered.connect(
            lambda _checked=False, target=path, kind=category:
            self._emit_delete(kind, target)
        )
        menu.exec(global_position)

    def _emit_delete(self, category: str, path: str) -> None:
        if category == "章节":
            self.delete_chapter_requested.emit(path)
        elif category == "角色":
            self.delete_character_requested.emit(path)
        else:
            self.delete_canon_requested.emit(category, path)

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

    def _canon_status(self, path: Path, kind: str) -> dict:
        key = str(path.resolve())
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            cached = self._canon_status_cache.get(key)
            if cached is not None and cached[0] == signature:
                result = dict(cached[1])
            else:
                entry = load_canon_entry(path, kind)
                result = {**entry_status(entry), "warnings": sum(bool(s.warning) for s in entry.sections)}
                self._canon_status_cache[key] = (signature, dict(result))
        except (OSError, ValueError):
            result = {"error": True}
        if kind == "character":
            if path.stem in self._alias_map:
                result.update(identity="alias", primary=self._alias_map[path.stem])
            elif path.stem in self._primary_names:
                result["identity"] = "primary"
        return result

    @staticmethod
    def _status_text(status: dict) -> tuple[str, str]:
        if status.get("error"):
            return "读取失败", "资料无法读取，原文件未被修改。"
        details = []
        if "missing_fields" in status:
            missing = status["missing_fields"]
            short = f"缺{missing}项" if missing else "已同步" if status.get("synced") else "待同步"
            details.append(f"基础档案：尚缺{missing}项预设字段" if missing else "基础档案：已填写")
            details.append("剧情状态：已同步" if status.get("synced") else "剧情状态：待同步")
        else:
            missing = status.get("missing_sections", 0)
            short = f"缺{missing}节" if missing else "已填写"
            details.append(f"资料内容：尚缺{missing}个模板分区" if missing else "模板分区：已填写")
        if status.get("identity") == "alias":
            details.append(f"身份：别名，归属「{status['primary']}」")
        elif status.get("identity") == "primary":
            details.append("身份：主身份")
        if status.get("warnings"):
            short = "待核对"
            details.append(f"{status['warnings']}个原文区块存在结构歧义，请打开核对。")
        return short, "\n".join(details)

    def _apply_importance_appearance(self, item: QTreeWidgetItem) -> None:
        self._apply_document_appearance(item)

    def _apply_document_appearance(self, item: QTreeWidgetItem) -> None:
        importance = item.data(0, self.IMPORTANCE_ROLE)
        status = item.data(0, self.CANON_STATUS_ROLE) or {}
        title = item.data(0, self.TITLE_ROLE) or item.text(0)
        short, details = self._status_text(status) if status else ("", "")
        # Loading policy remains the visible system badge; completeness is in its tooltip.
        badge = self.IMPORTANCE_LABELS.get(importance, "") if importance else short
        # 窄侧栏把状态压缩成着色圆点：颜色沿用宽侧栏的强调/静默规则，文字移入提示，
        # 名称列因此只承载名称与身份提示。
        name = title
        if status.get("identity"):
            name += " · 别名" if status["identity"] == "alias" else " · 主身份"
        item.setText(0, name)
        item.setText(1, self.STATUS_DOT if self._compact_mode and (status or importance) else badge)
        item.setTextAlignment(
            1,
            Qt.AlignmentFlag.AlignCenter if self._compact_mode
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
        )
        policy = self.IMPORTANCE_TOOLTIPS.get(importance, "")
        detail = "\n".join(part for part in (details, policy) if part)
        item.setToolTip(0, f"{title}\n{item.data(0, self.PATH_ROLE)}\n{detail}".rstrip())
        instruction = "资料状态：点击状态列、右键或按 Alt+Enter 查看。"
        item.setToolTip(1, f"{detail}\n{instruction}".strip())
        item.setData(1, Qt.ItemDataRole.AccessibleTextRole,
                     f"{title}的资料状态：{badge or '非核心'}")
        item.setData(1, Qt.ItemDataRole.AccessibleDescriptionRole,
                     f"{detail}\n{instruction}".strip())
        emphasized = importance in {"core", "always"} or status.get("warnings") or status.get("error")
        item.setForeground(1, QBrush(QColor(self._accent_color if emphasized else self._muted_color)))
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
            if group.data(0, self.PATH_ROLE) or group.data(0, self.FEATURE_ROLE):
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
