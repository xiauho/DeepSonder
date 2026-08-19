from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from core.project import NovelProject


class LeftPanel(QWidget):
    file_selected = Signal(str, str)
    new_chapter_requested = Signal()

    PATH_ROLE = int(Qt.ItemDataRole.UserRole)
    CATEGORY_ROLE = PATH_ROLE + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("navigationPanel")
        self.setMinimumWidth(230)
        self._project: NovelProject | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 16, 10, 12)
        layout.setSpacing(10)

        header = QFrame()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        title_box = QVBoxLayout()
        title_box.setSpacing(1)
        title = QLabel("故事资料库")
        title.setObjectName("panelTitle")
        self.project_label = QLabel("尚未打开项目")
        self.project_label.setObjectName("mutedLabel")
        title_box.addWidget(title)
        title_box.addWidget(self.project_label)
        add_btn = QPushButton("＋ 章节")
        add_btn.setObjectName("smallAccentButton")
        add_btn.setToolTip("新建章节")
        add_btn.clicked.connect(self.new_chapter_requested)
        header_layout.addLayout(title_box, 1)
        header_layout.addWidget(add_btn)
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
        self.tree.setIndentation(16)
        self.tree.setAnimated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.itemClicked.connect(self._on_item_clicked)
        layout.addWidget(self.tree, 1)

        hint = QLabel("单击打开 · Ctrl+S 保存 · Ctrl+K 专注")
        hint.setObjectName("navHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

    def set_project(self, project: NovelProject | None) -> None:
        self._project = project
        self.tree.clear()
        self.project_label.setText(project.name if project else "尚未打开项目")
        if project is None:
            return

        groups: list[tuple[str, str, list[Path]]] = [
            ("总大纲", "01", [project.outline_dir / "main_arc.md"]),
            ("章节", "02", project.list_chapters()),
            ("角色", "03", project.list_characters()),
            ("世界观", "04", project.list_world()),
            ("战力体系", "05", project.list_power()),
            ("时间线", "06", [project.canon_dir / "timeline.md"]),
        ]
        category_map = {"战力体系": "战力"}
        for label, number, paths in groups:
            existing = [path for path in paths if path.exists()]
            group = QTreeWidgetItem([f"{number}   {label}   {len(existing)}"])
            group.setData(0, self.CATEGORY_ROLE, category_map.get(label, label))
            group.setFlags(group.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(group)
            for path in existing:
                child_label = self._display_name(path)
                child = QTreeWidgetItem([child_label])
                child.setToolTip(0, str(path))
                child.setData(0, self.PATH_ROLE, str(path))
                child.setData(0, self.CATEGORY_ROLE, category_map.get(label, label))
                group.addChild(child)
            group.setExpanded(True)
        self._filter_tree(self.search.text())

    def select_path(self, path: Path) -> None:
        target = str(Path(path))
        iterator = self.tree.invisibleRootItem()
        for i in range(iterator.childCount()):
            group = iterator.child(i)
            for j in range(group.childCount()):
                child = group.child(j)
                if child.data(0, self.PATH_ROLE) == target:
                    self.tree.setCurrentItem(child)
                    self.tree.scrollToItem(child)
                    self._on_item_clicked(child, 0)
                    return

    def _on_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path_str = item.data(0, self.PATH_ROLE)
        if path_str:
            category = item.data(0, self.CATEGORY_ROLE) or ""
            self.file_selected.emit(category, path_str)

    def _filter_tree(self, query: str) -> None:
        needle = query.strip().casefold()
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            group = root.child(i)
            visible_children = 0
            for j in range(group.childCount()):
                child = group.child(j)
                visible = not needle or needle in child.text(0).casefold()
                child.setHidden(not visible)
                visible_children += int(visible)
            group.setHidden(bool(needle) and visible_children == 0)
            if needle and visible_children:
                group.setExpanded(True)

    @staticmethod
    def _display_name(path: Path) -> str:
        try:
            first_line = path.read_text(encoding="utf-8").splitlines()[0]
        except (OSError, IndexError):
            return path.stem
        if first_line.startswith("# "):
            return first_line[2:].strip() or path.stem
        return path.stem
