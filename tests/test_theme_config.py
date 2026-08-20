from unittest import TestCase

from core.config import DEFAULT_CONFIG
from ui.theme import DARK_COLORS, build_qss


class ThemeConfigTests(TestCase):
    def test_dark_theme_defaults_stay_in_sync(self) -> None:
        for key, value in DARK_COLORS.items():
            self.assertEqual(DEFAULT_CONFIG[key], value)

    def test_splitter_hover_style_uses_accent_color(self) -> None:
        qss = build_qss({"theme": "dark"})
        self.assertIn("QSplitter#mainSplitter::handle", qss)
        self.assertIn(DARK_COLORS["accent_color"], qss)
