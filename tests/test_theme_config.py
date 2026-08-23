from unittest import TestCase

from core.config import DEFAULT_CONFIG, _normalize_config
from ui.theme import DARK_COLORS, LIGHT_COLORS, build_qss, document_css


class ThemeConfigTests(TestCase):
    def test_light_theme_defaults_stay_in_sync(self) -> None:
        for key, value in LIGHT_COLORS.items():
            self.assertEqual(DEFAULT_CONFIG[key], value)

    def test_dark_theme_qss_uses_dark_accent(self) -> None:
        qss = build_qss({"theme": "dark"})
        self.assertIn("QSplitter#mainSplitter::handle", qss)
        self.assertIn(DARK_COLORS["accent_color"], qss)

    def test_message_box_qss_uses_theme_colors(self) -> None:
        qss = build_qss({"theme": "light"})
        self.assertIn("QMessageBox", qss)
        self.assertIn(LIGHT_COLORS["panel_color"], qss)
        self.assertIn(LIGHT_COLORS["text_color"], qss)

    def test_document_css_switches_browser_field_background(self) -> None:
        light_css = document_css({"theme": "light"})
        dark_css = document_css({"theme": "dark"})
        self.assertIn(LIGHT_COLORS["field_color"], light_css)
        self.assertIn(DARK_COLORS["field_color"], dark_css)
        self.assertNotEqual(light_css, dark_css)

    def test_ui_language_defaults_to_chinese(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["ui_language"], "zh-CN")

    def test_expansion_length_migrates_from_legacy_setting(self) -> None:
        config = {"continue_target_chars": 2600}
        _normalize_config(config)
        self.assertEqual(config["expand_target_chars"], 2600)
        self.assertNotIn("continue_target_chars", config)
