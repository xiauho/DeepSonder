"""Calm, editorial visual system for the desktop writing studio."""

from __future__ import annotations

DARK_COLORS = {
    "background_color": "#111419",
    "panel_color": "#181C22",
    "field_color": "#14181E",
    "border_color": "#2A3039",
    "text_color": "#E8E5DE",
    "muted_text_color": "#929AA7",
    "accent_color": "#D79A52",
    "selection_color": "#3A3026",
    "hover_color": "#232933",
}

LIGHT_COLORS = {
    "background_color": "#F3F1EC",
    "panel_color": "#FAF9F6",
    "field_color": "#FFFDF9",
    "border_color": "#DCD7CE",
    "text_color": "#24272D",
    "muted_text_color": "#747B84",
    "accent_color": "#A8642A",
    "selection_color": "#E9DDCF",
    "hover_color": "#ECE8E1",
}

QSS_TEMPLATE = r"""
* {
    font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif;
    font-size: __FONT_SIZE__px;
    color: __TEXT__;
}
QMainWindow, QDialog, QWidget#appRoot { background: __BG__; }
QWidget { background: transparent; }
QFrame#appHeader {
    background: __PANEL__;
    border-bottom: 1px solid __BORDER__;
}
QFrame#actionBar {
    background: __BG__;
    border-bottom: 1px solid __BORDER__;
}
QLabel#brandMark {
    background: #FFFFFF;
    color: __TEXT__;
    border: 1px solid __BORDER__;
    border-radius: 9px;
    min-width: 38px; max-width: 38px;
    min-height: 38px; max-height: 38px;
    font-family: "Segoe UI", "Microsoft YaHei UI", sans-serif;
    font-size: 15px;
    font-weight: 700;
}
QLabel#brandTitle { font-size: 18px; font-weight: 700; }
QLabel#panelTitle { font-size: 15px; font-weight: 700; }
QLabel#documentTitle { font-size: 22px; font-weight: 700; }
QLabel#mutedLabel, QLabel#navHint { color: __MUTED__; font-size: __SMALL_FONT_SIZE__px; }
QLabel#navHint { padding: 5px 0; }
QLabel#savedBadge, QLabel#dirtyBadge {
    padding: 4px 10px;
    border-radius: 10px;
    font-size: __SMALL_FONT_SIZE__px;
}
QLabel#savedBadge { color: #74B38A; background: __GOOD_BG__; }
QLabel#dirtyBadge { color: __ACCENT__; background: __SELECTION__; }
QLabel#aiStatus, QLabel#aiStatusBusy {
    border: 1px solid __BORDER__;
    border-radius: 14px;
    padding: 5px 11px;
    font-size: __SMALL_FONT_SIZE__px;
}
QLabel#aiStatus { color: #74B38A; background: __FIELD__; }
QLabel#aiStatusBusy { color: __ACCENT__; background: __SELECTION__; }

QWidget#navigationPanel, QWidget#inspectorPanel { background: __PANEL__; }
QWidget#navigationPanel { border-right: 1px solid __BORDER__; }
QWidget#inspectorPanel { border-left: 1px solid __BORDER__; }
QLineEdit#navigationSearch {
    border-radius: 10px;
    padding: 9px 11px;
}
QTreeWidget#projectTree {
    background: transparent;
    border: none;
    outline: none;
    padding-top: 2px;
}
QTreeWidget#projectTree::item {
    min-height: __TREE_ITEM_HEIGHT__px;
    border-radius: 7px;
    padding: 1px 6px;
    margin: 1px 0;
}
QTreeWidget#projectTree::item:hover { background: __HOVER__; }
QTreeWidget#projectTree::item:selected { background: __SELECTION__; color: __TEXT__; }
QTreeWidget#projectTree::branch { background: transparent; }

QFrame#editorHeader, QFrame#editorFooter { border: none; }
QFrame#findBar {
    background: __PANEL__;
    border: 1px solid __BORDER__;
    border-radius: 10px;
}
QPlainTextEdit#writingEditor {
    background: __FIELD__;
    border: 1px solid __BORDER__;
    border-radius: 12px;
    padding: 24px 32px;
    selection-background-color: __ACCENT__;
    selection-color: __ACCENT_TEXT__;
    font-family: "霞鹜文楷", "LXGW WenKai", "Microsoft YaHei UI", sans-serif;
    font-size: __EDITOR_FONT_SIZE__px;
    line-height: 1.8;
}
QPlainTextEdit#writingEditor:focus { border-color: __ACCENT__; }

QFrame#outputContainer {
    background: __PANEL__;
    border-top: 1px solid __BORDER__;
}
QPlainTextEdit#outputPanel, QTextBrowser#inspectorBrowser {
    background: __FIELD__;
    border: 1px solid __BORDER__;
    border-radius: 9px;
    padding: 10px;
    selection-background-color: __ACCENT__;
}
QPlainTextEdit#outputPanel { font-family: "Cascadia Mono", "Microsoft YaHei UI"; font-size: __SMALL_FONT_SIZE__px; }

QLineEdit, QSpinBox, QComboBox {
    background: __FIELD__;
    border: 1px solid __BORDER__;
    border-radius: 7px;
    padding: 7px 9px;
    selection-background-color: __ACCENT__;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: __ACCENT__; }
QPushButton, QToolButton {
    background: __PANEL__;
    border: 1px solid __BORDER__;
    border-radius: 8px;
    padding: 7px 12px;
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
QToolButton#ghostButton:hover, QPushButton#ghostButton:hover { background: __HOVER__; }
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

QMenuBar { background: __PANEL__; border-bottom: 1px solid __BORDER__; padding: 2px 8px; }
QMenuBar::item { padding: 5px 9px; border-radius: 5px; }
QMenuBar::item:selected { background: __HOVER__; }
QMenu { background: __PANEL__; border: 1px solid __BORDER__; border-radius: 8px; padding: 6px; }
QMenu::item { padding: 7px 28px 7px 12px; border-radius: 5px; }
QMenu::item:selected { background: __SELECTION__; }
QStatusBar { background: __PANEL__; border-top: 1px solid __BORDER__; padding: 3px 10px; }
QStatusBar::item { border: none; }
QProgressBar { background: __FIELD__; border: none; border-radius: 4px; }
QProgressBar::chunk { background: __ACCENT__; border-radius: 4px; }
QSplitter::handle { background: __BORDER__; width: 1px; height: 1px; }
QSplitter::handle:hover { background: __ACCENT__; }

QDialog { background: __BG__; }
QGroupBox {
    border: 1px solid __BORDER__;
    border-radius: 10px;
    margin-top: 12px;
    padding: 15px 10px 10px 10px;
    font-weight: 700;
}
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 6px; color: __ACCENT__; }
QDialogButtonBox QPushButton { min-width: 80px; }

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
    return value if isinstance(value, str) and value else default


def _mix(hex_color: str, target: str, amount: float) -> str:
    def rgb(value: str) -> tuple[int, int, int]:
        value = value.lstrip("#")
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    first, second = rgb(hex_color), rgb(target)
    mixed = tuple(round(a + (b - a) * amount) for a, b in zip(first, second))
    return "#" + "".join(f"{channel:02X}" for channel in mixed)


def build_qss(config: dict | None = None) -> str:
    config = config or {}
    light = config.get("theme", "dark") == "light"
    colors = dict(LIGHT_COLORS if light else DARK_COLORS)
    for key in colors:
        colors[key] = _color(config, key, colors[key])

    font_size = max(10, min(26, int(config.get("ui_font_size", 14) or 14)))
    editor_size = max(12, min(36, int(config.get("editor_font_size", 17) or 17)))
    accent_text = "#FFFFFF" if light else "#17120D"
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
        "__ACCENT_HOVER__": _mix(colors["accent_color"], "#FFFFFF" if not light else "#000000", 0.12),
        "__GOOD_BG__": _mix(colors["panel_color"], "#2E7D4F", 0.25),
    }
    qss = QSS_TEMPLATE
    for token, value in replacements.items():
        qss = qss.replace(token, value)
    return qss


def apply_theme(app, config: dict | None = None) -> None:
    app.setStyleSheet(build_qss(config))
