"""Synchronize theme and appearance changes across the desktop shell."""

from __future__ import annotations

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication

from ui.icons import refresh_button_icons, set_button_icon
from ui.theme import apply_theme, colors_for


class AppearanceController(QObject):
    """Apply settings-driven appearance changes to all theme-aware widgets."""

    def __init__(
        self,
        *,
        root,
        inspector,
        left_panel,
        settings_page,
        theme_button,
        action_icon_buttons: dict,
        ai_creation_button=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.root = root
        self.inspector = inspector
        self.left_panel = left_panel
        self.settings_page = settings_page
        self.theme_button = theme_button
        self.ai_creation_button = ai_creation_button
        self.action_icon_buttons = action_icon_buttons
        self.parent = parent
        self.config: dict = {}

    def apply(self, config: dict) -> None:
        self.config = dict(config)
        app = QApplication.instance()
        if isinstance(app, QApplication):
            apply_theme(app, self.config)
        self.inspector.set_theme(self.config)
        editor = getattr(self.root, "editor", None)
        if editor is not None and hasattr(editor, "set_theme"):
            editor.set_theme(self.config)
        self.left_panel.set_theme(self.config)
        self.settings_page.synchronize_config(self.config)
        self.refresh_icons()

    def refresh_icons(self) -> None:
        palette = colors_for(self.config)
        muted = palette["muted_text_color"]
        refresh_button_icons(self.root, muted)
        for button in self.action_icon_buttons.values():
            color = palette["primary_text_color"] if button.objectName() == "accentButton" else muted
            button.set_icon_color(color)
        theme_icon = "dark_mode" if self.config.get("theme") == "light" else "light_mode"
        set_button_icon(self.theme_button, theme_icon, muted, 17)
        if self.ai_creation_button is not None:
            set_button_icon(self.ai_creation_button, "auto_awesome", palette["primary_text_color"], 17)
