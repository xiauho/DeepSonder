"""Modeless first-project setup dialog for story canon entries."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)

from core.project import NovelProject
from core.project_data import ProjectDataStore


class ProjectSetupDialog(QDialog):
    """Guide new authors through optional canon setup without blocking editing."""

    new_entry_requested = Signal(str)
    entry_open_requested = Signal(str)
    done_requested = Signal()

    ENTRY_TYPES = (
        ("character", "角色", "先建立主角、对手或关键配角"),
        ("world", "世界观", "先确定故事舞台、规则和历史背景"),
        ("power", "体系设定", "先确定能力、空间或其他体系的规则与边界"),
    )

    def __init__(self, project: NovelProject, parent=None) -> None:
        super().__init__(parent)
        self._project = project
        self._lists: dict[str, QListWidget] = {}
        self._counts: dict[str, QLabel] = {}
        self.setWindowTitle("初始化故事资料")
        self.setModal(False)
        self.setMinimumWidth(560)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel("先把故事的核心设定放进项目")
        title.setObjectName("pageTitle")
        subtitle = QLabel(
            "角色、世界观和体系设定都可以只创建草稿，之后仍能在资料树中继续完善。"
        )
        subtitle.setObjectName("mutedLabel")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        core_card = QFrame()
        core_card.setObjectName("sectionCard")
        core_layout = QHBoxLayout(core_card)
        core_layout.setContentsMargins(14, 10, 14, 10)
        core_text = QVBoxLayout()
        core_title = QLabel("常驻核心规则")
        core_title.setObjectName("sectionTitle")
        core_text.addWidget(core_title)
        core_hint = QLabel("全书始终生效的底层规则；建议先填写限制、代价和不可违背的原则。")
        core_hint.setObjectName("mutedLabel")
        core_hint.setWordWrap(True)
        core_text.addWidget(core_hint)
        core_layout.addLayout(core_text, 1)
        open_core = QPushButton("打开编辑")
        open_core.setObjectName("secondaryButton")
        open_core.clicked.connect(self._open_core_rules)
        core_layout.addWidget(open_core)
        root.addWidget(core_card)

        for kind, label, hint in self.ENTRY_TYPES:
            card = QFrame()
            card.setObjectName("sectionCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(14, 12, 14, 12)
            card_layout.setSpacing(7)

            header = QHBoxLayout()
            title_label = QLabel(label)
            title_label.setObjectName("sectionTitle")
            count_label = QLabel("0 个条目")
            count_label.setObjectName("mutedLabel")
            button = QPushButton(f"+ 新建{label}")
            button.setObjectName("secondaryButton")
            button.clicked.connect(
                lambda _checked=False, entry_kind=kind: self.new_entry_requested.emit(
                    entry_kind
                )
            )
            header.addWidget(title_label)
            header.addWidget(count_label)
            header.addStretch(1)
            header.addWidget(button)
            card_layout.addLayout(header)

            hint_label = QLabel(hint)
            hint_label.setObjectName("mutedLabel")
            card_layout.addWidget(hint_label)

            entries = QListWidget()
            entries.setObjectName("projectSetupEntries")
            entries.setMinimumHeight(34)
            entries.setMaximumHeight(94)
            entries.itemDoubleClicked.connect(
                lambda item, _column=0: self.entry_open_requested.emit(
                    str(item.data(Qt.ItemDataRole.UserRole))
                )
            )
            card_layout.addWidget(entries)
            self._lists[kind] = entries
            self._counts[kind] = count_label
            root.addWidget(card)

        footer = QHBoxLayout()
        footer.addWidget(
            QLabel("可以先跳过，之后从故事资料页的“新建资料”继续。"), 1
        )
        done = QPushButton("完成，进入写作台")
        done.setObjectName("accentButton")
        done.clicked.connect(self.done_requested)
        footer.addWidget(done)
        root.addLayout(footer)

        self.refresh(project)

    def refresh(self, project: NovelProject | None = None) -> None:
        if project is not None:
            self._project = project
        store = ProjectDataStore(self._project)
        sources = {
            "character": store.list_characters,
            "world": store.list_world,
            "power": store.list_power_entries,
        }
        for kind, entries in self._lists.items():
            entries.clear()
            paths = sources[kind]()
            for path in paths:
                item = QListWidgetItem(store.chapter_display_name(Path(path)))
                item.setToolTip(str(path))
                item.setData(Qt.ItemDataRole.UserRole, str(path))
                entries.addItem(item)
            self._counts[kind].setText(f"{len(paths)} 个条目")

    def _open_core_rules(self) -> None:
        path = ProjectDataStore(self._project).core_power_path
        if path.exists():
            self.entry_open_requested.emit(str(path))
