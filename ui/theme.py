"""Shared design tokens and the single Qt stylesheet used by Novalist."""

from __future__ import annotations

import re
from pathlib import Path

from PySide6.QtGui import QColor, QPalette

from core.theme_tokens import DARK_COLORS, LIGHT_COLORS

QSS_TEMPLATE = r"""
* {
    font-family: "Microsoft YaHei UI", "Inter", "Hanken Grotesk", "Segoe UI", sans-serif;
    font-size: __FONT_SIZE__px;
    color: __TEXT__;
}
QMainWindow, QWidget#appRoot { background: __BG__; }
QWidget { background: transparent; }
QDialog { background: __BG__; }
QDialog#trashDialog { background: __BG__; color: __TEXT__; }

QWidget#primarySidebar {
    background: __PANEL__;
    border-right: 1px solid __BORDER__;
}
QLabel#brandTitle, QLabel#eyebrow {
    font-family: "Hanken Grotesk", "Inter", "Microsoft YaHei UI", sans-serif;
}
QLabel#pageTitle {
    font-family: "Microsoft YaHei UI", "Inter", sans-serif;
    font-size: 22px;
    font-weight: 800;
}
QLabel#brandTitle { font-size: 22px; font-weight: 800; }
QLabel#brandSubtitle, QLabel#mutedLabel, QLabel#navHint {
    color: __MUTED__;
    font-size: __SMALL_FONT_SIZE__px;
}
QLabel#documentTitle, QLabel#sectionTitle {
    font-family: "Microsoft YaHei UI", "Inter", sans-serif;
}
QLabel#documentTitle { font-size: 22px; font-weight: 750; }
QLabel#panelTitle { font-size: 15px; font-weight: 750; }
QLabel#eyebrow {
    color: __ACCENT__;
    font-size: __SMALL_FONT_SIZE__px;
    font-weight: 700;
}

QFrame#appHeader {
    background: __PANEL__;
    border-bottom: 1px solid __BORDER__;
}
QFrame#actionBar {
    background: __BG__;
    border-bottom: 1px solid __BORDER__;
}
QLabel#aiStatus, QLabel#aiStatusBusy {
    border: 1px solid __BORDER__;
    border-radius: 14px;
    padding: 5px 11px;
    font-size: __SMALL_FONT_SIZE__px;
    font-weight: 700;
}
QLabel#aiStatus { color: __GOOD__; background: __GOOD_BG__; }
QLabel#aiStatusBusy { color: __ACCENT__; background: __SELECTION__; }

QFrame#primaryNav { background: transparent; border: none; }
QPushButton#primaryNavButton, QPushButton#primaryNavActive {
    text-align: left;
    border: none;
    border-radius: 7px;
    padding: 10px 12px;
    min-height: 20px;
    font-weight: 650;
}
QPushButton#primaryNavButton { background: transparent; color: __MUTED__; }
QPushButton#primaryNavButton:hover { background: __HOVER__; color: __TEXT__; }
QPushButton#primaryNavActive {
    background: __SELECTION__;
    color: __ACCENT__;
    border-left: 3px solid __ACCENT__;
    padding-left: 9px;
}
QLabel#buttonIcon {
    font-family: "Material Symbols Outlined";
    font-size: 17px;
    color: __MUTED__;
}
QLabel#buttonText {
    font-family: "Microsoft YaHei UI", "Inter", sans-serif;
    font-size: __FONT_SIZE__px;
    font-weight: 600;
    color: __TEXT__;
}
QPushButton#primaryNavButton QLabel#buttonIcon { color: __MUTED__; }
QPushButton#primaryNavButton QLabel#buttonText { color: __MUTED__; }
QPushButton#primaryNavButton:hover QLabel#buttonIcon,
QPushButton#primaryNavButton:hover QLabel#buttonText { color: __TEXT__; }
QPushButton#primaryNavActive QLabel#buttonIcon,
QPushButton#primaryNavActive QLabel#buttonText { color: __ACCENT__; }
QPushButton#primaryNavActive QLabel#buttonText { font-weight: 700; }
QPushButton#accentButton QLabel#buttonIcon,
QPushButton#accentButton QLabel#buttonText { color: __ACCENT_TEXT__; }
QPushButton#smallAccentButton QLabel#buttonIcon,
QPushButton#smallAccentButton QLabel#buttonText { color: __ACCENT__; }
QFrame#projectCard {
    background: __FIELD__;
    border: 1px solid __BORDER__;
    border-radius: 9px;
}
QFrame#navDivider { background: __BORDER__; border: none; max-height: 1px; }
QLabel#projectCardName { font-weight: 700; }

QWidget#pageSurface, QWidget#dashboardPage, QWidget#reportsPage,
QWidget#exportPage, QWidget#settingsPage { background: __BG__; }
QScrollArea#pageScroll, QWidget#pageContent { background: transparent; border: none; }
QFrame#pageHeader, QFrame#sectionCard, QFrame#statCard, QFrame#emptyState,
QFrame#settingsSection, QFrame#reportSummaryCard {
    background: __PANEL__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
}
QFrame#pageHeader { border: none; background: transparent; }
QLabel#sectionTitle { font-size: 16px; font-weight: 750; }
QLabel#statValue {
    color: __ACCENT__;
    font-family: "JetBrains Mono", "Microsoft YaHei UI", monospace;
    font-size: 24px;
    font-weight: 800;
}
QLabel#statCaption { color: __MUTED__; font-size: __SMALL_FONT_SIZE__px; }
QLabel#emptyIcon { color: __ACCENT__; font-size: 28px; font-weight: 700; }

QWidget#navigationPanel, QWidget#inspectorPanel {
    background: __PANEL__;
}
QWidget#navigationPanel { border-right: 1px solid __BORDER__; }
QWidget#inspectorPanel { border-left: 1px solid __BORDER__; }
QLineEdit#navigationSearch {
    border-radius: 7px;
    padding: 9px 11px;
}
QTreeWidget#projectTree {
    background: transparent;
    border: none;
    outline: none;
    show-decoration-selected: 0;
    padding-top: 2px;
}
QTreeWidget#projectTree::item {
    min-height: __TREE_ITEM_HEIGHT__px;
    border-radius: 6px;
    padding: 2px 10px;
    margin: 2px 0;
}
QTreeWidget#projectTree::branch { background: transparent; }
QTreeWidget#projectTree::item:hover { background: __HOVER__; }
QTreeWidget#projectTree::item:pressed { background: __SELECTION__; }
QTreeWidget#projectTree::item:selected,
QTreeWidget#projectTree::item:selected:active,
QTreeWidget#projectTree::item:selected:!active {
    background: __SELECTION__;
    color: __TEXT__;
    border: none;
}
QListWidget#chapterList, QListWidget#trashList {
    background: __FIELD__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    outline: none;
}
QListWidget#chapterList::item, QListWidget#trashList::item {
    min-height: __TREE_ITEM_HEIGHT__px;
    padding: 2px 8px;
    border-radius: 6px;
}
QListWidget#chapterList::item:hover, QListWidget#trashList::item:hover {
    background: __HOVER__;
}
QListWidget#chapterList::item:pressed, QListWidget#trashList::item:pressed {
    background: __SELECTION__;
}
QListWidget#chapterList::item:selected, QListWidget#trashList::item:selected {
    background: __SELECTION__;
    color: __TEXT__;
    border: none;
}
QToolButton#panelToggleButton {
    background: transparent;
    color: __MUTED__;
    border: 1px solid transparent;
    border-radius: 5px;
    min-width: 24px; max-width: 24px;
    min-height: 24px; max-height: 24px;
    padding: 0;
    font-size: 17px;
}
QToolButton#panelToggleButton:hover {
    background: __HOVER__;
    color: __TEXT__;
    border-color: __BORDER__;
}

QFrame#editorHeader, QFrame#editorFooter { border: none; }
QFrame#findBar {
    background: __PANEL__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
}
QPlainTextEdit#writingEditor {
    background: __FIELD__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    padding: 24px 32px;
    selection-background-color: __ACCENT__;
    selection-color: __ACCENT_TEXT__;
    font-family: "Microsoft YaHei UI", "Source Serif 4", "霞鹜文楷", "LXGW WenKai", sans-serif;
    font-size: __EDITOR_FONT_SIZE__px;
}
QPlainTextEdit#writingEditor:focus { border-color: __ACCENT__; }
QFrame#outputContainer {
    background: __PANEL__;
    border-top: 1px solid __BORDER__;
}
QPlainTextEdit#outputPanel, QTextBrowser#inspectorBrowser, QTextBrowser#pageBrowser {
    background: __FIELD__;
    border: 1px solid __BORDER__;
    border-radius: 7px;
    padding: 10px;
    selection-background-color: __ACCENT__;
}
QPlainTextEdit#outputPanel { font-family: "JetBrains Mono", "Cascadia Mono", "Microsoft YaHei UI"; font-size: __SMALL_FONT_SIZE__px; }

QLabel#savedBadge, QLabel#dirtyBadge {
    padding: 4px 10px;
    border-radius: 10px;
    font-size: __SMALL_FONT_SIZE__px;
}
QLabel#savedBadge { color: __GOOD__; background: __GOOD_BG__; }
QLabel#dirtyBadge { color: __ACCENT__; background: __SELECTION__; }

QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: __FIELD__;
    border: 1px solid __BORDER__;
    border-radius: 7px;
    padding: 7px 9px;
    padding-right: 34px;
    selection-background-color: __ACCENT__;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus { border-color: __ACCENT__; }
QSpinBox, QDoubleSpinBox { padding-right: 30px; }
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border;
    width: 26px;
    height: 13px;
    border: none;
    background: transparent;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-position: top right;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-position: bottom right;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background: __HOVER__;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    image: url("__SPIN_UP_ARROW__");
    width: 14px;
    height: 14px;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    image: url("__SPIN_DOWN_ARROW__");
    width: 14px;
    height: 14px;
}
QComboBox { combobox-popup: 0; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 30px;
    border: none;
    background: transparent;
}
QComboBox::drop-down:hover { background: __HOVER__; border: none; }
QComboBox::down-arrow {
    image: url("__COMBO_ARROW__");
    width: 18px;
    height: 18px;
}
QComboBox QAbstractItemView {
    background: __PANEL__;
    color: __TEXT__;
    border: 1px solid __BORDER__;
    outline: none;
    padding: 4px;
    selection-background-color: __SELECTION__;
    selection-color: __TEXT__;
}
QCheckBox {
    spacing: 8px;
    min-height: 24px;
}
QCheckBox::indicator,
QListWidget#chapterList::indicator {
    width: 18px;
    height: 18px;
    border: 1px solid __CHECK_BORDER__;
    border-radius: 4px;
    background: __CHECK_BG__;
}
QCheckBox::indicator:hover,
QListWidget#chapterList::indicator:hover {
    border-color: __ACCENT__;
    background: __HOVER__;
}
QCheckBox::indicator:checked,
QListWidget#chapterList::indicator:checked {
    border-color: __ACCENT__;
    background: __ACCENT__;
    image: url("__CHECKMARK__");
}
QCheckBox::indicator:disabled,
QListWidget#chapterList::indicator:disabled {
    border-color: __BORDER__;
    background: __BG__;
}
QPushButton, QToolButton {
    background: __PANEL__;
    border: 1px solid __BORDER__;
    border-radius: 6px;
    padding: 6px 11px;
    min-height: 18px;
}
QPushButton:hover, QToolButton:hover { background: __HOVER__; border-color: __ACCENT__; }
QPushButton:pressed, QToolButton:pressed { background: __SELECTION__; }
QPushButton:disabled, QToolButton:disabled { color: __MUTED__; border-color: __BORDER__; }
QToolButton#accentButton, QPushButton#accentButton {
    background: __ACCENT__;
    color: __ACCENT_TEXT__;
    border-color: __ACCENT__;
    font-weight: 700;
    padding-left: 17px; padding-right: 17px;
}
QToolButton#accentButton:hover, QPushButton#accentButton:hover { background: __ACCENT_HOVER__; }
QToolButton#secondaryButton, QPushButton#secondaryButton { background: __FIELD__; }
QToolButton#ghostButton, QPushButton#ghostButton { background: transparent; border-color: transparent; }
QToolButton#ghostButton:hover, QPushButton#ghostButton:hover { background: __SELECTION__; }
QPushButton#smallAccentButton {
    background: __SELECTION__;
    color: __ACCENT__;
    border-color: transparent;
    padding: 5px 9px;
}
QPushButton#focusExitButton {
    background: __SELECTION__;
    color: __ACCENT__;
    border: 1px solid __ACCENT__;
    border-radius: 9px;
    padding: 7px 13px;
}
QPushButton#focusExitButton:hover { background: __ACCENT__; color: __ACCENT_TEXT__; }
QPushButton#dangerButton { color: __DANGER__; }
QPushButton#linkButton { background: transparent; border: none; color: __ACCENT__; text-align: left; }

QTabWidget::pane { border: none; background: transparent; top: -1px; }
QTabBar::tab {
    background: transparent;
    color: __MUTED__;
    border: none;
    border-bottom: 2px solid transparent;
    padding: 8px 10px;
}
QTabBar::tab:hover { color: __TEXT__; }
QTabBar::tab:selected { color: __ACCENT__; border-bottom-color: __ACCENT__; }
QGroupBox {
    border: 1px solid __BORDER__;
    border-radius: 10px;
    margin-top: 12px;
    padding: 15px 10px 10px 10px;
    font-weight: 700;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: __ACCENT__; }
QDialogButtonBox QPushButton { min-width: 80px; }
QMessageBox {
    background: __PANEL__;
    color: __TEXT__;
    border: 1px solid __BORDER__;
}
QMessageBox QLabel {
    background: transparent;
    color: __TEXT__;
}
QMessageBox QPushButton {
    background: __FIELD__;
    color: __TEXT__;
    border: 1px solid __BORDER__;
    border-radius: 6px;
    padding: 7px 18px;
    min-width: 80px;
}
QMessageBox QPushButton:hover {
    background: __HOVER__;
    border-color: __ACCENT__;
}
QMessageBox QPushButton:default {
    background: __ACCENT__;
    color: __ACCENT_TEXT__;
    border-color: __ACCENT__;
}

QMenuBar { background: __PANEL__; border-bottom: 1px solid __BORDER__; padding: 2px 8px; }
QMenuBar::item { padding: 5px 9px; border-radius: 5px; }
QMenuBar::item:selected { background: __HOVER__; }
QMenu { background: __PANEL__; border: 1px solid __BORDER__; border-radius: 6px; padding: 6px; }
QMenu::item { padding: 7px 28px 7px 12px; border-radius: 5px; }
QMenu::item:selected { background: __SELECTION__; }
QStatusBar { background: __PANEL__; border-top: 1px solid __BORDER__; padding: 3px 10px; }
QStatusBar::item { border: none; }
QProgressBar { background: __FIELD__; border: none; border-radius: 4px; }
QProgressBar::chunk { background: __ACCENT__; border-radius: 4px; }
QSplitter::handle { background: __BORDER__; width: 1px; height: 1px; }
QSplitter::handle:hover { background: __ACCENT__; }
QSplitter#mainSplitter::handle { background: __BORDER__; width: 5px; }
QSplitter#mainSplitter::handle:hover { background: __ACCENT__; }
QSplitter#outerSplitter::handle { background: __BORDER__; height: 5px; }
QSplitter#outerSplitter::handle:hover { background: __ACCENT__; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: __BORDER__; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: __MUTED__; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: transparent; height: 9px; }
QScrollBar::handle:horizontal { background: __BORDER__; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""


def _color(config: dict, key: str, default: str) -> str:
    value = config.get(key)
    if isinstance(value, str) and re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
        return value.upper()
    return default


def _mix(hex_color: str, target: str, amount: float) -> str:
    def rgb(value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))

    first, second = rgb(hex_color), rgb(target)
    mixed = tuple(round(a + (b - a) * amount) for a, b in zip(first, second))
    return "#" + "".join(f"{channel:02X}" for channel in mixed)


def colors_for(config: dict | None = None) -> dict[str, str]:
    config = config or {}
    light = config.get("theme", "light") == "light"
    preset = LIGHT_COLORS if light else DARK_COLORS
    return {key: _color(config, key, value) for key, value in preset.items()}


def build_qss(config: dict | None = None) -> str:
    config = config or {}
    light = config.get("theme", "light") == "light"
    colors = colors_for(config)
    try:
        font_size = max(10, min(26, int(config.get("ui_font_size", 14) or 14)))
    except (TypeError, ValueError):
        font_size = 14
    try:
        editor_size = max(12, min(36, int(config.get("editor_font_size", 16) or 16)))
    except (TypeError, ValueError):
        editor_size = 16
    accent_text = "#FFFFFF" if light else "#08111E"
    replacements = {
        "__FONT_SIZE__": str(font_size),
        "__SMALL_FONT_SIZE__": str(max(10, font_size - 2)),
        "__EDITOR_FONT_SIZE__": str(editor_size),
        "__TREE_ITEM_HEIGHT__": str(max(30, font_size + 15)),
        "__BG__": colors["background_color"],
        "__PANEL__": colors["panel_color"],
        "__FIELD__": colors["field_color"],
        "__BORDER__": colors["border_color"],
        "__TEXT__": colors["text_color"],
        "__MUTED__": colors["muted_text_color"],
        "__ACCENT__": colors["accent_color"],
        "__SELECTION__": colors["selection_color"],
        "__HOVER__": colors["hover_color"],
        "__ACCENT_TEXT__": accent_text,
        "__ACCENT_HOVER__": _mix(colors["accent_color"], "#FFFFFF" if light else "#000000", 0.12),
        "__GOOD__": "#278457" if light else "#7BD39B",
        "__GOOD_BG__": _mix(colors["panel_color"], "#278457", 0.13),
        "__DANGER__": "#C13D49" if light else "#FF8D98",
        "__CHECK_BORDER__": colors["text_color"],
        "__CHECK_BG__": colors["panel_color"] if light else colors["field_color"],
        "__CHECKMARK__": str(Path(__file__).resolve().parents[1] / "assets" / "checkmark.svg").replace("\\", "/"),
        "__COMBO_ARROW__": str(
            Path(__file__).resolve().parents[1]
            / "assets"
            / ("chevron-down-light.svg" if light else "chevron-down-dark.svg")
        ).replace("\\", "/"),
        "__SPIN_UP_ARROW__": str(
            Path(__file__).resolve().parents[1]
            / "assets"
            / ("chevron-up-light.svg" if light else "chevron-up-dark.svg")
        ).replace("\\", "/"),
        "__SPIN_DOWN_ARROW__": str(
            Path(__file__).resolve().parents[1]
            / "assets"
            / ("chevron-down-light.svg" if light else "chevron-down-dark.svg")
        ).replace("\\", "/"),
    }
    qss = QSS_TEMPLATE
    for token, value in replacements.items():
        qss = qss.replace(token, value)
    return qss


def document_css(config: dict | None = None) -> str:
    """Return a theme-aware stylesheet for QTextBrowser documents."""
    colors = colors_for(config)
    is_light = not config or config.get("theme", "light") == "light"
    return (
        "body{font-family:'Microsoft YaHei UI','Inter';color:%s;background:%s;line-height:1.65;}"
        "h2{font-family:'Microsoft YaHei UI','Hanken Grotesk';font-size:18px;margin:5px 0 10px;color:%s;}"
        "h3{font-size:14px;margin:16px 0 7px;color:%s;}"
        ".eyebrow{font-size:11px;color:%s;letter-spacing:1px;}"
        ".muted{color:%s;}.good{color:%s;}"
        ".card{background:%s;border:1px solid %s;border-radius:8px;padding:9px;margin:5px 0;}"
        ".chip{background:%s;color:%s;border-radius:8px;padding:3px 7px;margin-right:4px;}"
        ".metric{display:inline-block;background:%s;border:1px solid %s;padding:7px;margin:3px;}"
        ".metric b{font-size:17px;color:%s;}.metric span{font-size:10px;color:%s;margin-left:4px;}"
        ".report{line-height:1.75;}li{margin-bottom:5px;}"
    ) % (
        colors["text_color"], colors["field_color"], colors["text_color"],
        colors["text_color"], colors["accent_color"], colors["muted_text_color"],
        "#278457" if is_light else "#7BD39B",
        colors["panel_color"], colors["border_color"], colors["selection_color"],
        colors["accent_color"], colors["panel_color"], colors["border_color"],
        colors["accent_color"], colors["muted_text_color"],
    )


def apply_theme(app, config: dict | None = None) -> None:
    colors = colors_for(config)
    light = (config or {}).get("theme", "light") == "light"
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors["background_color"]))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors["text_color"]))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors["field_color"]))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors["panel_color"]))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors["text_color"]))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors["field_color"]))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors["text_color"]))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors["accent_color"]))
    palette.setColor(
        QPalette.ColorRole.HighlightedText,
        QColor("#FFFFFF" if light else "#08111E"),
    )
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors["panel_color"]))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors["text_color"]))
    app.setPalette(palette)
    app.setStyleSheet(build_qss(config))
