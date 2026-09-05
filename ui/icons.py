"""Local Stitch typography and Material Symbols helpers for Qt widgets."""

from __future__ import annotations

from PySide6.QtCore import QSize, QTimer, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QAbstractButton, QLabel, QPushButton, QHBoxLayout, QSizePolicy

from core.resources import resource_root

PROJECT_ROOT = resource_root()
FONT_ROOT = PROJECT_ROOT / "assets" / "fonts"

# Only the normal weights used by the application are loaded. The source
# package contains italic and unused weights as well, but loading all of them
# slows startup and gives Qt more duplicate font families to resolve.
STITCH_FONT_FILES = (
    "font-17.woff2",  # Inter 400
    "font-33.woff2",  # Inter 500
    "font-32.woff2",  # Inter 600
    "font-31.woff2",  # Inter 700
    "font-13.woff2",  # Hanken Grotesk 500
    "font-11.woff2",  # Hanken Grotesk 600
    "font-18.woff2",  # Hanken Grotesk 700
    "font-14.woff2",  # Hanken Grotesk 800
    "font-52.woff2",  # JetBrains Mono 400
    "font-45.woff2",  # JetBrains Mono 500
    "font-46.woff2",  # JetBrains Mono 600
    "font-47.woff2",  # JetBrains Mono 700
    "font-68.woff2",  # Source Serif 4 400
    "font-69.woff2",  # Source Serif 4 500
    "font-74.woff2",  # Source Serif 4 600
    "font-75.woff2",  # Source Serif 4 700
    "font-58.woff2",  # Material Symbols Outlined 400
    "font-56.woff2",  # Material Symbols Outlined 500
)
MATERIAL_SYMBOL_FONT_FILES = frozenset(("font-58.woff2", "font-56.woff2"))

_loaded_font_ids: dict[str, int] = {}
_material_symbols_available = False


def load_stitch_fonts() -> dict[str, int]:
    """Load the bundled WOFF2 files once and return their Qt font IDs."""
    global _material_symbols_available

    for filename in STITCH_FONT_FILES:
        if filename in _loaded_font_ids:
            continue
        path = FONT_ROOT / filename
        if not path.is_file():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        if font_id >= 0:
            _loaded_font_ids[filename] = font_id
            if filename in MATERIAL_SYMBOL_FONT_FILES:
                families = QFontDatabase.applicationFontFamilies(font_id)
                _material_symbols_available |= "Material Symbols Outlined" in families
    return dict(_loaded_font_ids)


def material_symbols_available() -> bool:
    """Return whether Material Symbols can safely render ligature names."""
    load_stitch_fonts()
    if _material_symbols_available:
        return True
    # A user-installed copy is a valid fallback, but never assume it exists.
    return "Material Symbols Outlined" in QFontDatabase.families()


def _font(size: int = 18, weight: int = 400) -> QFont:
    load_stitch_fonts()
    font = QFont("Material Symbols Outlined")
    font.setPixelSize(size)
    font.setWeight(QFont.Weight(weight))
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font


def material_icon(name: str, color: str = "#63748A", size: int = 18) -> QIcon:
    """Render a Material Symbols ligature into a scalable-enough Qt icon."""
    if not material_symbols_available():
        return QIcon()

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    painter.setPen(QColor(color))
    painter.setFont(_font(size, 400))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, name)
    painter.end()
    return QIcon(pixmap)


class IconTextButton(QPushButton):
    """A button with a local Material Symbols icon and themeable text."""

    def __init__(self, icon_name: str, text: str, parent=None, centered: bool = True):
        super().__init__(parent)
        self.setAccessibleName(text)
        self._centered = centered
        self._rail_anchor_ratio: float | None = None
        self.setProperty("material_icon", icon_name)
        self.setProperty("material_icon_size", 17)
        # Never expose the ligature name as visible UI text if its font is
        # unavailable; the button label remains usable in that case.
        icon = QLabel(icon_name if material_symbols_available() else "", self)
        icon.setObjectName("buttonIcon")
        icon.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedWidth(20)
        icon.setMinimumHeight(20)
        icon.setFont(_font(17, 400))
        label = QLabel(text, self)
        label.setObjectName("buttonText")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        label.setMinimumHeight(20)
        if centered:
            label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
            # The text is the anchor. The icon is positioned relative to it
            # instead of being included in the centering calculation.
            self._layout = None
        else:
            layout = QHBoxLayout(self)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(label, 1, Qt.AlignmentFlag.AlignVCenter)
            self._layout = layout
        self._icon_label = icon
        self._text_label = label
        self.sync_geometry()

    def set_icon_color(self, color: str) -> None:
        self._icon_label.setStyleSheet(f"color: {color};")

    def refresh_style(self) -> None:
        """Re-evaluate QSS for the button and its styled child labels."""
        widgets = (self, self._icon_label, self._text_label)
        for widget in widgets:
            widget.style().unpolish(widget)
        for widget in widgets:
            widget.style().polish(widget)
            widget.update()
        self.updateGeometry()
        self.sync_geometry()

    def set_label(self, text: str) -> None:
        """Update the visible label without disturbing the centered layout."""
        self._text_label.setText(text)
        self.setAccessibleName(text)
        self.updateGeometry()
        self.sync_geometry()

    def set_rail_anchor(self, ratio: float = 0.25) -> None:
        """Align the icon to a fixed fraction of the full button width."""
        self._rail_anchor_ratio = max(0.0, min(1.0, ratio))
        self.updateGeometry()
        self.sync_geometry()

    def setText(self, text: str) -> None:  # noqa: N802 - Qt API
        super().setText(text)
        if hasattr(self, "_text_label"):
            self.set_label(text)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt API
        if not self._centered:
            return super().sizeHint()
        text_width = self._text_label.fontMetrics().horizontalAdvance(self._text_label.text())
        line_height = self._text_label.fontMetrics().lineSpacing()
        # Keep enough room for a small left icon margin while the text stays
        # centered. The right side is intentionally quieter by design.
        return QSize(max(64, text_width + 64), max(32, line_height + 12))

    def sync_geometry(self) -> None:
        """Give the icon and text identical vertical slots after QSS changes."""
        slot_height = max(20, self._text_label.fontMetrics().lineSpacing())
        if self._rail_anchor_ratio is not None:
            text_width = self._text_label.fontMetrics().horizontalAdvance(self._text_label.text())
            icon_x = round(self.width() * self._rail_anchor_ratio)
            text_x = icon_x + 28
            label_width = max(text_width, self.width() - text_x - 10)
            y = round((self.height() - slot_height) / 2)
            self._icon_label.setGeometry(icon_x, y, 20, slot_height)
            self._text_label.setGeometry(text_x, y, label_width, slot_height)
            return
        if not self._centered:
            self._icon_label.setFixedHeight(slot_height)
            self._text_label.setFixedHeight(slot_height)
            return
        text_width = self._text_label.fontMetrics().horizontalAdvance(self._text_label.text())
        text_x = round((self.width() - text_width) / 2)
        icon_x = text_x - 8 - 20
        y = round((self.height() - slot_height) / 2)
        self._icon_label.setGeometry(icon_x, y, 20, slot_height)
        self._text_label.setGeometry(text_x, y, text_width, slot_height)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().resizeEvent(event)
        self.sync_geometry()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt API
        super().showEvent(event)
        # QSS font metrics are finalised during polish/show, not construction.
        QTimer.singleShot(0, self.sync_geometry)


def set_button_icon(button: QAbstractButton, name: str, color: str = "#63748A", size: int = 18) -> None:
    """Attach a Material Symbols icon to a standard Qt button."""
    load_stitch_fonts()
    button.setProperty("material_icon", name)
    button.setProperty("material_icon_size", size)
    button.setIcon(material_icon(name, color, size))
    button.setIconSize(QSize(size, size))


def refresh_button_icons(root, color: str = "#63748A") -> None:
    """Re-render icon-button pixmaps after a theme change."""
    for button in root.findChildren(QAbstractButton):
        if isinstance(button, IconTextButton):
            button.sync_geometry()
            button.updateGeometry()
            continue
        name = button.property("material_icon")
        if not name:
            continue
        size = int(button.property("material_icon_size") or 18)
        set_button_icon(button, str(name), color, size)
