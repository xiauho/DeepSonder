from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.icons import IconTextButton


class PrimaryNavigation(QWidget):
    """One persistent primary navigation for every top-level page."""

    NAV_ANCHOR_RATIO = 0.10
    quick_open_requested = Signal()
    route_requested = Signal(str)
    new_project_requested = Signal()
    trash_requested = Signal()

    ROUTES = (
        ("dashboard", "space_dashboard", "项目"),
        ("writing", "edit_note", "写作台"),
        ("canon", "auto_stories", "故事资料"),
        ("memory", "psychology", "故事记忆"),
        ("reports", "analytics", "检查报告"),
        ("export", "ios_share", "导出"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("primarySidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(76)
        self.buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 14, 6, 10)
        layout.setSpacing(4)
        self.quick_open_button = self._rail_button("search", "查找", "快速打开章节与资料（Ctrl+P）")
        self.quick_open_button.clicked.connect(self.quick_open_requested)
        layout.addWidget(self.quick_open_button)
        # Project identity lives beside the document directory, not in the rail.
        self.project_name = QLabel(self)
        self.project_name.hide()
        short_labels = {"writing": "写作", "canon": "资料", "memory": "记忆", "reports": "报告"}
        for route, icon_name, label in self.ROUTES:
            button = self._rail_button(icon_name, short_labels.get(route, label), label)
            button.clicked.connect(lambda _checked=False, target=route: self.route_requested.emit(target))
            layout.addWidget(button)
            self.buttons[route] = button
        layout.addStretch(1)
        trash = self._rail_button("delete_sweep", "回收站", "打开回收站")
        trash.clicked.connect(lambda _checked=False: self.trash_requested.emit())
        self.buttons["trash"] = self.trash_button = trash
        layout.addWidget(trash)
        settings = self._rail_button("settings", "设置", "应用设置")
        settings.clicked.connect(lambda: self.route_requested.emit("settings"))
        self.buttons["settings"] = settings
        layout.addWidget(settings)

        self.set_trash_enabled(False)
        self.set_active("dashboard")

    def _rail_button(self, icon: str, label: str, tooltip: str) -> IconTextButton:
        button = IconTextButton(icon, label)
        button.set_vertical()
        button.setFixedHeight(56)
        button.setObjectName("primaryNavButton")
        button.setToolTip(tooltip)
        button.setAccessibleName(tooltip)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def set_project(self, name: str | None) -> None:
        self.project_name.setText(name or "尚未打开项目")
        self.set_trash_enabled(name is not None)

    def set_trash_enabled(self, enabled: bool) -> None:
        self.trash_button.setEnabled(bool(enabled))

    def set_active(self, route: str) -> None:
        for key, button in self.buttons.items():
            active = key == route
            button.setObjectName("primaryNavActive" if active else "primaryNavButton")
            button.refresh_style()
