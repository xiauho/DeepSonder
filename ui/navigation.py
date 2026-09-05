from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.icons import IconTextButton


class PrimaryNavigation(QWidget):
    """One persistent primary navigation for every top-level page."""

    NAV_ANCHOR_RATIO = 0.10
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
        self.setFixedWidth(236)
        self.buttons: dict[str, QPushButton] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 14, 16)
        layout.setSpacing(7)

        brand_text = QVBoxLayout()
        brand_text.setSpacing(3)
        title = QLabel("Novalist")
        title.setObjectName("brandTitle")
        subtitle = QLabel("本地小说创作平台")
        subtitle.setObjectName("brandSubtitle")
        brand_text.addWidget(title)
        brand_text.addWidget(subtitle)
        layout.addLayout(brand_text)

        self.project_card = QFrame()
        self.project_card.setObjectName("projectCard")
        project_layout = QVBoxLayout(self.project_card)
        project_layout.setContentsMargins(11, 10, 11, 10)
        project_layout.setSpacing(3)
        project_caption = QLabel("当前项目")
        project_caption.setObjectName("mutedLabel")
        self.project_name = QLabel("尚未打开项目")
        self.project_name.setObjectName("projectCardName")
        self.project_name.setWordWrap(True)
        project_layout.addWidget(project_caption)
        project_layout.addWidget(self.project_name)
        layout.addWidget(self.project_card)

        new_button = IconTextButton("add", "新建项目", centered=True)
        new_button.setObjectName("accentButton")
        new_button.clicked.connect(self.new_project_requested)
        layout.addWidget(new_button)
        layout.addSpacing(10)

        nav_label = QLabel("工作区")
        nav_label.setObjectName("mutedLabel")
        layout.addWidget(nav_label)
        nav_frame = QFrame()
        nav_frame.setObjectName("primaryNav")
        nav_layout = QVBoxLayout(nav_frame)
        nav_layout.setContentsMargins(0, 0, 0, 0)
        nav_layout.setSpacing(3)
        for route, icon_name, label in self.ROUTES:
            button = IconTextButton(icon_name, label)
            button.set_rail_anchor(self.NAV_ANCHOR_RATIO)
            button.setObjectName("primaryNavButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, target=route: self.route_requested.emit(target))
            nav_layout.addWidget(button)
            self.buttons[route] = button
        nav_layout.addStretch(1)
        layout.addWidget(nav_frame, 1)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setObjectName("navDivider")
        layout.addWidget(divider)
        trash = IconTextButton("delete_sweep", "回收站")
        trash.set_rail_anchor(self.NAV_ANCHOR_RATIO)
        trash.setObjectName("primaryNavButton")
        trash.setCursor(Qt.CursorShape.PointingHandCursor)
        trash.setToolTip("打开回收站")
        trash.clicked.connect(lambda _checked=False: self.trash_requested.emit())
        self.buttons["trash"] = trash
        self.trash_button = trash
        layout.addWidget(trash)

        settings = IconTextButton("settings", "设置")
        settings.set_rail_anchor(self.NAV_ANCHOR_RATIO)
        settings.setObjectName("primaryNavButton")
        settings.setCursor(Qt.CursorShape.PointingHandCursor)
        settings.clicked.connect(lambda: self.route_requested.emit("settings"))
        self.buttons["settings"] = settings
        layout.addWidget(settings)
        local = QLabel("本地优先 · dsh headless")
        local.setObjectName("navHint")
        local.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(local)

        self.set_trash_enabled(False)
        self.set_active("dashboard")

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
