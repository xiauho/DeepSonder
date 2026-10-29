from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QMenu, QPushButton, QVBoxLayout, QWidget

from ui.icons import IconTextButton


class PrimaryNavigation(QWidget):
    """One persistent primary navigation for every top-level page."""

    NAV_ANCHOR_RATIO = 0.10
    quick_open_requested = Signal()
    route_requested = Signal(str)

    ROUTES = (
        ("writing", "edit_note", "写作台"),
        ("canon", "auto_stories", "故事资料"),
        ("reports", "fact_check", "检查报告与故事记忆"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("primarySidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(64)
        self._review_route = "reports"
        self.buttons: dict[str, QPushButton] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 14, 6, 10)
        layout.setSpacing(4)
        self.quick_open_button = self._rail_button("search", "查找", "快速打开章节与资料（Ctrl+P）")
        self.quick_open_button.clicked.connect(self.quick_open_requested)
        layout.addWidget(self.quick_open_button)
        self.project_menu = QMenu(self)
        self.project_button = self._rail_button("folder_open", "项目", "项目概览、新建、打开与导出")
        self.project_button.setMenu(self.project_menu)
        layout.addWidget(self.project_button)
        self.buttons["project"] = self.project_button
        short_labels = {"writing": "写作", "canon": "资料", "reports": "检查"}
        for route, icon_name, label in self.ROUTES:
            button = self._rail_button(icon_name, short_labels.get(route, label), label)
            button.clicked.connect(lambda _checked=False, target=route:
                self.route_requested.emit(self._review_route if target == "reports" else target))
            layout.addWidget(button)
            self.buttons[route] = button
        layout.addStretch(1)
        self.more_menu = QMenu(self)
        self.more_button = self._rail_button("more_horiz", "更多", "回收站与命令查找")
        self.more_button.setMenu(self.more_menu)
        self.buttons["more"] = self.more_button
        layout.addWidget(self.more_button)
        settings = self._rail_button("settings", "设置", "应用设置")
        settings.clicked.connect(lambda: self.route_requested.emit("settings"))
        self.buttons["settings"] = settings
        layout.addWidget(settings)

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
        self.project_button.setToolTip((name or "尚未打开项目") + " · 项目概览、新建、打开与导出")

    def set_active(self, route: str) -> None:
        if route in {"memory", "reports"}:
            self._review_route = route
        route = {"dashboard": "project", "export": "project", "memory": "reports", "timeline": "canon"}.get(route, route)
        for key, button in self.buttons.items():
            active = key == route
            button.setObjectName("primaryNavActive" if active else "primaryNavButton")
            button.refresh_style()
