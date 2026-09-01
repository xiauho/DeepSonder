from unittest import TestCase

from core.config import (
    AI_CONTEXT_HISTORY_CHAPTERS_MAX,
    DEFAULT_CONFIG,
    DSH_FILE_PROMPT_BUDGET_DEFAULT,
    DSH_FILE_PROMPT_BUDGET_MAX,
    _normalize_config,
)
from ui.theme import DARK_COLORS, LIGHT_COLORS, build_qss, document_css


class ThemeConfigTests(TestCase):
    def test_light_theme_defaults_stay_in_sync(self) -> None:
        for key, value in LIGHT_COLORS.items():
            self.assertEqual(DEFAULT_CONFIG[key], value)

    def test_dark_theme_qss_uses_dark_accent(self) -> None:
        qss = build_qss({"theme": "dark"})
        self.assertIn("QSplitter#mainSplitter::handle", qss)
        self.assertIn(DARK_COLORS["accent_color"], qss)

    def test_foreshadowing_selection_list_uses_themed_indicator(self) -> None:
        light_qss = build_qss({"theme": "light"})
        dark_qss = build_qss({"theme": "dark"})

        selector = "QListWidget#foreshadowingSelectionList::indicator"
        self.assertIn(selector, light_qss)
        self.assertIn(selector, dark_qss)
        self.assertIn("QListWidget#foreshadowingSelectionList {", light_qss)
        self.assertIn("selection-background-color: transparent", light_qss)
        self.assertIn("QListWidget#foreshadowingSelectionList::item:hover", light_qss)
        self.assertIn("QListWidget#powerSelectionList {", light_qss)
        self.assertIn("QListWidget#powerSelectionList::indicator", light_qss)
        # The light theme uses the panel color for an unchecked indicator;
        # the dark theme uses the field color to preserve contrast.
        self.assertIn(LIGHT_COLORS["panel_color"], light_qss)
        self.assertIn(DARK_COLORS["field_color"], dark_qss)

    def test_message_box_qss_uses_theme_colors(self) -> None:
        qss = build_qss({"theme": "light"})
        self.assertIn("QMessageBox", qss)
        self.assertIn(LIGHT_COLORS["panel_color"], qss)
        self.assertIn(LIGHT_COLORS["text_color"], qss)

    def test_trash_dialog_has_opaque_theme_surfaces(self) -> None:
        qss = build_qss({"theme": "light"})
        self.assertIn("QDialog#trashDialog", qss)
        self.assertIn("QListWidget#trashList", qss)
        self.assertLess(qss.index("QWidget { background: transparent; }"), qss.index("QDialog { background:"))
        self.assertIn(LIGHT_COLORS["field_color"], qss)

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

    def test_history_chapter_setting_is_bounded(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["ai_context_history_chapters"], 5)
        config = {"ai_context_history_chapters": 99}
        _normalize_config(config)
        self.assertEqual(AI_CONTEXT_HISTORY_CHAPTERS_MAX, 20)
        self.assertEqual(config["ai_context_history_chapters"], 20)

        config = {"ai_context_history_chapters": -3}
        _normalize_config(config)
        self.assertEqual(config["ai_context_history_chapters"], 0)

    def test_prompt_transport_settings_are_normalized(self) -> None:
        self.assertEqual(DEFAULT_CONFIG["dsh_prompt_transport"], "auto")
        self.assertEqual(
            DEFAULT_CONFIG["dsh_file_prompt_budget"],
            DSH_FILE_PROMPT_BUDGET_DEFAULT,
        )

        config = {
            "dsh_prompt_transport": "invalid",
            "dsh_file_prompt_budget": 999_999,
        }
        _normalize_config(config)
        self.assertEqual(config["dsh_prompt_transport"], "auto")
        self.assertEqual(
            config["dsh_file_prompt_budget"],
            DSH_FILE_PROMPT_BUDGET_MAX,
        )
